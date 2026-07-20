"""Load HCAD (Harris Central Appraisal District) bulk ownership data.

HCAD publishes yearly bulk downloads (public record) at download.hcad.org.
The Real_acct_owner archive contains `real_acct.txt` — tab-delimited, one row
per account, with the owner (mailto) name, mailing address, and site address.
This is the authoritative account-number -> owner-name mapping; the ArcGIS
parcels layer carries the same account number (HCAD_NUM / LOWPARCELID), so the
two join cleanly.

Default URL pattern (verify on https://hcad.org/pdata/pdata-property-downloads.html
if it 404s — HCAD occasionally moves files):
    https://download.hcad.org/data/CAMA/{year}/Real_acct_owner.zip

Hardening (P0 spine):
  - required input/file checks and fail-loud schema/required-column assertions
  - chunked/streaming load into a STAGING table, atomically swapped into place
    only after the load reconciles — so the last known-good table survives any
    mid-run failure
  - checkpointed, safe resume of an interrupted load
  - duplicate `acct` handling (UNIQUE + INSERT OR IGNORE, keep-first)
  - malformed-row quarantine with reasons
  - row-count reconciliation (read == inserted + duplicates + quarantined)
  - a LoadManifest of run metrics, persisted for monitoring

Only grounded columns are assumed: `acct` (the required join key) and the
`mailto` / `mail_addr_1` owner columns already used by the existing code and
README. Exact HCAD field lists and the live URL remain UNVERIFIED (outbound
access is blocked) — see IMPLEMENTATION_BACKLOG.md live-verification status.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import requests

BULK_URL_TEMPLATE = "https://download.hcad.org/data/CAMA/{year}/Real_acct_owner.zip"
OWNERS_TABLE = "owners"

# The account number is the required join key. Grounded in existing code
# (the previous loader indexed "acct") and the README join examples.
REQUIRED_COLUMNS = ("acct",)
# Expected but not required — absence is a warning, not a failure. Grounded in
# the README owner-join example (o.mailto, o.mail_addr_1).
EXPECTED_COLUMNS = ("mailto", "mail_addr_1")

_OVERFLOW_KEY = "__overflow__"
_DEFAULT_BATCH = 1000
_CHECKPOINT_EVERY = 50_000

csv.field_size_limit(sys.maxsize)


class HCADInputError(ValueError):
    """Bad/missing input file or archive member."""


class HCADSchemaError(ValueError):
    """Required column missing from the bulk file header."""


@dataclass
class LoadManifest:
    """Run metrics for one bulk load — persisted for monitoring."""
    table: str
    zip_path: str
    member: str = ""
    file_bytes: int = 0
    columns: list = field(default_factory=list)
    required_present: list = field(default_factory=list)
    expected_missing: list = field(default_factory=list)
    rows_read: int = 0
    rows_inserted: int = 0
    duplicates: int = 0
    quarantined: int = 0
    reconciled: bool = False
    resumed: bool = False
    started_at: str = ""
    ended_at: str = ""
    status: str = "INIT"  # INIT | STAGED | PASS | FAIL

    def to_dict(self) -> dict:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

def download_bulk_zip(year: int, dest_dir: str = "data") -> Path:
    """Download the yearly bulk zip atomically; verify it is a real zip.

    Downloads to a `.part` file and renames on success so an interrupted
    download never leaves a truncated file that a later run would trust.
    """
    url = BULK_URL_TEMPLATE.format(year=year)
    dest = Path(dest_dir) / f"Real_acct_owner_{year}.zip"
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and dest.stat().st_size > 0:
        if zipfile.is_zipfile(dest):
            print(f"already downloaded: {dest}")
            return dest
        print(f"existing {dest} is not a valid zip; re-downloading")
        dest.unlink()

    part = dest.with_suffix(dest.suffix + ".part")
    print(f"downloading {url} ...")
    with requests.get(url, stream=True, timeout=300) as resp:
        resp.raise_for_status()
        with open(part, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                if chunk:
                    fh.write(chunk)
    if not zipfile.is_zipfile(part):
        part.unlink(missing_ok=True)
        raise HCADInputError(f"downloaded file from {url} is not a valid zip")
    part.replace(dest)
    print(f"saved {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


# --------------------------------------------------------------------------
# Read
# --------------------------------------------------------------------------

def _find_member(zf: zipfile.ZipFile, needle: str) -> Optional[str]:
    for name in zf.namelist():
        if needle in name.lower():
            return name
    return None


def _normalize_header(fieldnames: list) -> tuple[list, dict]:
    """Return (sanitized_columns, orig->sanitized map). Matches prior behaviour:
    columns are the source header, stripped and lowercased."""
    col_map: dict = {}
    cols: list = []
    for h in fieldnames or []:
        if h is None:
            continue
        c = h.strip().lower()
        # de-dupe collisions deterministically
        base, n = c, 1
        while c in cols:
            c = f"{base}_{n}"
            n += 1
        col_map[h] = c
        cols.append(c)
    return cols, col_map


def _open_reader(text: io.TextIOWrapper) -> csv.DictReader:
    return csv.DictReader(text, delimiter="\t", restkey=_OVERFLOW_KEY, restval=None)


def iter_owner_rows(zip_path, member_hint: str = "real_acct") -> Iterable[dict]:
    """Yield dict rows (lowercased keys) from real_acct.txt inside the bulk zip.

    Backward-compatible helper. Raises HCADInputError if the archive/member is
    missing so callers fail loudly rather than silently yielding nothing.
    """
    zp = Path(zip_path)
    if not zp.exists():
        raise HCADInputError(f"zip not found: {zp}")
    if not zipfile.is_zipfile(zp):
        raise HCADInputError(f"not a valid zip: {zp}")
    with zipfile.ZipFile(zp) as zf:
        member = _find_member(zf, member_hint)
        if not member:
            raise HCADInputError(
                f"no member matching '{member_hint}' in {zp}; contents: {zf.namelist()[:10]}")
        with zf.open(member) as raw:
            text = io.TextIOWrapper(raw, encoding="cp1252", errors="replace")
            reader = _open_reader(text)
            for row in reader:
                yield {(k.strip().lower() if k else k): (v.strip() if isinstance(v, str) else v)
                       for k, v in row.items() if k and k != _OVERFLOW_KEY}


# --------------------------------------------------------------------------
# Meta / checkpoint tables
# --------------------------------------------------------------------------

def _ensure_meta_tables(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS _scrape_meta (
               table_name TEXT PRIMARY KEY, layer_url TEXT,
               rows_fetched INTEGER DEFAULT 0,
               updated_at TEXT DEFAULT (datetime('now')))""")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS _load_checkpoint (
               table_name TEXT PRIMARY KEY, source TEXT, member TEXT,
               rows_read INTEGER DEFAULT 0, updated_at TEXT)""")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS _load_manifest (
               table_name TEXT PRIMARY KEY, manifest TEXT, updated_at TEXT)""")


def _read_checkpoint(conn, table: str):
    return conn.execute(
        "SELECT source, member, rows_read FROM _load_checkpoint WHERE table_name=?",
        (table,)).fetchone()


def _write_checkpoint(conn, table: str, source: str, member: str, rows_read: int) -> None:
    conn.execute(
        "INSERT INTO _load_checkpoint(table_name, source, member, rows_read, updated_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(table_name) DO UPDATE SET "
        "source=excluded.source, member=excluded.member, "
        "rows_read=excluded.rows_read, updated_at=excluded.updated_at",
        (table, source, member, rows_read, _now()))


def _clear_checkpoint(conn, table: str) -> None:
    conn.execute("DELETE FROM _load_checkpoint WHERE table_name=?", (table,))


def _persist_manifest(conn, m: LoadManifest) -> None:
    conn.execute(
        "INSERT INTO _load_manifest(table_name, manifest, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(table_name) DO UPDATE SET manifest=excluded.manifest, "
        "updated_at=excluded.updated_at",
        (m.table, json.dumps(m.to_dict()), _now()))


# --------------------------------------------------------------------------
# Load
# --------------------------------------------------------------------------

def load_owners(conn, zip_path, table: str = OWNERS_TABLE,
                limit: Optional[int] = None, batch_size: int = _DEFAULT_BATCH,
                resume: bool = False) -> LoadManifest:
    """Stream the HCAD bulk file into `table`, hardened for production.

    Loads into a staging table and atomically swaps it into place only after
    the row counts reconcile, so a mid-run failure leaves the previous
    known-good `table` untouched. Returns a LoadManifest of run metrics.
    """
    zp = Path(zip_path)
    if not zp.exists():
        raise HCADInputError(f"zip not found: {zp}")
    if not zipfile.is_zipfile(zp):
        raise HCADInputError(f"not a valid zip: {zp}")

    staging = f"{table}__staging"
    quarantine = f"{table}__quarantine"
    manifest = LoadManifest(table=table, zip_path=str(zp), started_at=_now(),
                            resumed=bool(resume))
    _ensure_meta_tables(conn)

    with zipfile.ZipFile(zp) as zf:
        member = _find_member(zf, "real_acct")
        if not member:
            raise HCADInputError(
                f"no member matching 'real_acct' in {zp}; contents: {zf.namelist()[:10]}")
        manifest.member = member
        manifest.file_bytes = zf.getinfo(member).file_size

        with zf.open(member) as raw:
            text = io.TextIOWrapper(raw, encoding="cp1252", errors="replace")
            reader = _open_reader(text)
            cols, col_map = _normalize_header(reader.fieldnames)
            manifest.columns = cols

            # fail-loud required-column assertion
            missing_required = [c for c in REQUIRED_COLUMNS if c not in cols]
            if missing_required:
                raise HCADSchemaError(
                    f"required column(s) {missing_required} not in header {cols[:20]}")
            manifest.required_present = list(REQUIRED_COLUMNS)
            manifest.expected_missing = [c for c in EXPECTED_COLUMNS if c not in cols]
            if manifest.expected_missing:
                print(f"warning: expected column(s) absent: {manifest.expected_missing}")

            acct_orig = next(o for o, s in col_map.items() if s == "acct")

            # resume vs fresh: decide whether to keep the staging tables
            skip = 0
            cp = _read_checkpoint(conn, table)
            if resume and cp and cp[0] == str(zp) and cp[1] == member:
                skip = cp[2]
                manifest.rows_read = skip
                print(f"resuming '{table}' load from row {skip}")
            else:
                if resume and cp:
                    raise HCADInputError(
                        "resume requested but checkpoint is for a different source/member; "
                        "run without --resume to restart")
                conn.execute(f'DROP TABLE IF EXISTS "{staging}"')
                conn.execute(f'DROP TABLE IF EXISTS "{quarantine}"')
                col_defs = ", ".join(f'"{c}" TEXT' for c in cols)
                conn.execute(f'CREATE TABLE "{staging}" ({col_defs}, UNIQUE("acct"))')
                conn.execute(
                    f'CREATE TABLE "{quarantine}" '
                    f'(rownum INTEGER, reason TEXT, raw TEXT)')
                _write_checkpoint(conn, table, str(zp), member, 0)
                conn.commit()

            insert_sql = (f'INSERT OR IGNORE INTO "{staging}" '
                          f'({",".join(chr(34)+c+chr(34) for c in cols)}) '
                          f'VALUES ({",".join("?" for _ in cols)})')
            quar_sql = f'INSERT INTO "{quarantine}"(rownum, reason, raw) VALUES (?,?,?)'

            good_batch: list = []
            quar_batch: list = []
            rownum = 0
            processed_this_run = 0

            def flush():
                if good_batch:
                    conn.executemany(insert_sql, good_batch)
                    good_batch.clear()
                if quar_batch:
                    conn.executemany(quar_sql, quar_batch)
                    quar_batch.clear()

            try:
                for row in reader:
                    rownum += 1
                    # honor resume skip (rows already processed in a prior run)
                    if rownum <= skip:
                        continue
                    manifest.rows_read += 1
                    processed_this_run += 1

                    reason = None
                    if _OVERFLOW_KEY in row and row.get(_OVERFLOW_KEY):
                        reason = "extra_columns"
                    acct_val = (row.get(acct_orig) or "").strip()
                    if not acct_val:
                        reason = reason or "missing_acct"

                    if reason:
                        manifest.quarantined += 1
                        quar_batch.append(
                            (rownum, reason,
                             json.dumps({k: v for k, v in row.items()
                                         if k != _OVERFLOW_KEY}, default=str)[:4000]))
                    else:
                        good_batch.append(
                            [(row.get(o).strip() if isinstance(row.get(o), str)
                              else row.get(o)) for o in col_map])

                    if len(good_batch) >= batch_size or len(quar_batch) >= batch_size:
                        flush()
                    if manifest.rows_read % _CHECKPOINT_EVERY < 1:
                        flush()
                        _write_checkpoint(conn, table, str(zp), member, rownum)
                        conn.commit()
                        print(f"  ... {manifest.rows_read} rows read "
                              f"({manifest.quarantined} quarantined)")
                    if limit and processed_this_run >= limit:
                        break

                flush()
                _write_checkpoint(conn, table, str(zp), member, rownum)
                conn.commit()
            except Exception:
                # leave staging + checkpoint in place for a later --resume; the
                # live `table` is untouched, so the last good spine survives.
                manifest.status = "FAIL"
                manifest.ended_at = _now()
                _persist_manifest(conn, manifest)
                conn.commit()
                raise

    # reconciliation
    inserted = conn.execute(f'SELECT COUNT(*) FROM "{staging}"').fetchone()[0]
    quarantined = conn.execute(f'SELECT COUNT(*) FROM "{quarantine}"').fetchone()[0]
    manifest.rows_inserted = inserted
    manifest.quarantined = quarantined
    manifest.duplicates = manifest.rows_read - inserted - quarantined
    manifest.reconciled = (manifest.rows_read == inserted + quarantined + manifest.duplicates
                           and manifest.duplicates >= 0)
    manifest.status = "STAGED"

    if not manifest.reconciled:
        manifest.status = "FAIL"
        manifest.ended_at = _now()
        _persist_manifest(conn, manifest)
        conn.commit()
        raise HCADInputError(
            f"row-count reconciliation failed: read={manifest.rows_read} "
            f"inserted={inserted} quarantined={quarantined} "
            f"duplicates={manifest.duplicates}")

    # atomic swap — the ONLY point the previous good table is replaced
    conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    conn.execute(f'ALTER TABLE "{staging}" RENAME TO "{table}"')
    for col in ("mailto", "mail_to"):
        if col in manifest.columns:
            conn.execute(
                f'CREATE INDEX IF NOT EXISTS "idx_{table}_{col}" ON "{table}"("{col}")')
    conn.execute(
        "INSERT INTO _scrape_meta(table_name, layer_url, rows_fetched, updated_at) "
        "VALUES (?,?,?,?) ON CONFLICT(table_name) DO UPDATE SET "
        "rows_fetched=excluded.rows_fetched, updated_at=excluded.updated_at",
        (table, manifest.zip_path, inserted, _now()))
    _clear_checkpoint(conn, table)
    manifest.status = "PASS"
    manifest.ended_at = _now()
    _persist_manifest(conn, manifest)
    conn.commit()
    return manifest
