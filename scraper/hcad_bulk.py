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
"""

from __future__ import annotations

import csv
import io
import sys
import zipfile
from pathlib import Path
from typing import Iterable, Optional

import requests

BULK_URL_TEMPLATE = "https://download.hcad.org/data/CAMA/{year}/Real_acct_owner.zip"
OWNERS_TABLE = "owners"

csv.field_size_limit(sys.maxsize)


def download_bulk_zip(year: int, dest_dir: str = "data") -> Path:
    url = BULK_URL_TEMPLATE.format(year=year)
    dest = Path(dest_dir) / f"Real_acct_owner_{year}.zip"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"already downloaded: {dest}")
        return dest
    print(f"downloading {url} ...")
    with requests.get(url, stream=True, timeout=300) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                fh.write(chunk)
    print(f"saved {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def _find_member(zf: zipfile.ZipFile, needle: str) -> Optional[str]:
    for name in zf.namelist():
        if needle in name.lower():
            return name
    return None


def iter_owner_rows(zip_path: Path, member_hint: str = "real_acct") -> Iterable[dict]:
    """Yield dict rows from real_acct.txt inside the bulk zip."""
    with zipfile.ZipFile(zip_path) as zf:
        member = _find_member(zf, member_hint)
        if not member:
            raise FileNotFoundError(
                f"no member matching '{member_hint}' in {zip_path}; contents: {zf.namelist()[:10]}"
            )
        with zf.open(member) as raw:
            text = io.TextIOWrapper(raw, encoding="cp1252", errors="replace")
            reader = csv.DictReader(text, delimiter="\t")
            for row in reader:
                yield {k.strip().lower(): (v.strip() if v else v) for k, v in row.items() if k}


def load_owners(conn, zip_path: Path, table: str = OWNERS_TABLE, limit: Optional[int] = None) -> int:
    """Create/replace the owners table from the HCAD bulk file."""
    rows = iter_owner_rows(zip_path)
    first = next(iter(rows), None)
    if first is None:
        return 0
    cols = list(first.keys())
    conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    col_defs = ", ".join('"{}" TEXT'.format(c) for c in cols)
    conn.execute(f'CREATE TABLE "{table}" ({col_defs})')
    sql = f'INSERT INTO "{table}" VALUES ({",".join("?" for _ in cols)})'

    def as_row(d: dict):
        return [d.get(c) for c in cols]

    total = 0
    batch = [as_row(first)]
    for row in rows:
        batch.append(as_row(row))
        if len(batch) >= 1000:
            conn.executemany(sql, batch)
            total += len(batch)
            batch = []
            if total % 100000 < 1000:
                print(f"  ... {total} owner rows loaded")
        if limit and total >= limit:
            break
    if batch:
        conn.executemany(sql, batch)
        total += len(batch)
    # index the account number and owner name for fast joins/searches
    for col in ("acct", "mailto", "mail_to"):
        if col in cols:
            conn.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_{col} ON "{table}"("{col}")')
    conn.commit()
    return total
