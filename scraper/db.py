"""SQLite storage for scraped ArcGIS features and HCAD owner records."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

DEFAULT_DB = "data/dynastykey.db"

_ESRI_TYPE_MAP = {
    "esriFieldTypeOID": "INTEGER",
    "esriFieldTypeSmallInteger": "INTEGER",
    "esriFieldTypeInteger": "INTEGER",
    "esriFieldTypeBigInteger": "INTEGER",
    "esriFieldTypeSingle": "REAL",
    "esriFieldTypeDouble": "REAL",
    "esriFieldTypeDate": "INTEGER",
}


def connect(db_path: str = DEFAULT_DB) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS _scrape_meta (
               table_name TEXT PRIMARY KEY,
               layer_url  TEXT,
               rows_fetched INTEGER DEFAULT 0,
               updated_at TEXT DEFAULT (datetime('now'))
           )"""
    )
    return conn


def sanitize(name: str) -> str:
    clean = re.sub(r"\W+", "_", name.strip()).strip("_").lower()
    return clean or "col"


def create_feature_table(
    conn: sqlite3.Connection, table: str, esri_fields: list[dict], with_geometry: bool
) -> dict[str, str]:
    """Create a table from an ArcGIS field list. Returns {esri_name: column_name}."""
    mapping: dict[str, str] = {}
    cols = []
    for f in esri_fields:
        col = sanitize(f["name"])
        while col in mapping.values():
            col += "_"
        mapping[f["name"]] = col
        cols.append(f'"{col}" {_ESRI_TYPE_MAP.get(f.get("type"), "TEXT")}')
    if with_geometry:
        cols += ['"_point_lon" REAL', '"_point_lat" REAL', '"_geometry" TEXT']
    conn.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(cols)})')
    return mapping


def insert_features(
    conn: sqlite3.Connection,
    table: str,
    mapping: dict[str, str],
    features: Iterable[dict],
    with_geometry: bool,
    point_fn,
    batch_size: int = 500,
    progress_every: int = 5000,
    layer_url: str = "",
) -> int:
    cols = list(mapping.values())
    if with_geometry:
        cols += ["_point_lon", "_point_lat", "_geometry"]
    placeholders = ",".join("?" for _ in cols)
    col_list = ",".join('"{}"'.format(c) for c in cols)
    sql = f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})'

    total = 0
    batch = []
    for feat in features:
        attrs = feat.get("attributes") or {}
        row = [attrs.get(esri_name) for esri_name in mapping]
        if with_geometry:
            lon, lat = point_fn(feat)
            geom = feat.get("geometry")
            row += [lon, lat, json.dumps(geom) if geom else None]
        batch.append(row)
        if len(batch) >= batch_size:
            conn.executemany(sql, batch)
            total += len(batch)
            batch = []
            conn.execute(
                "INSERT INTO _scrape_meta(table_name, layer_url, rows_fetched) VALUES (?,?,?) "
                "ON CONFLICT(table_name) DO UPDATE SET rows_fetched=rows_fetched+?, "
                "updated_at=datetime('now')",
                (table, layer_url, total, batch_size),
            )
            conn.commit()
            if total % progress_every < batch_size:
                print(f"  ... {total} rows stored in '{table}'")
    if batch:
        conn.executemany(sql, batch)
        total += len(batch)
        conn.commit()
    return total


class FeatureReconcileError(RuntimeError):
    """Staged feature count fell short of the layer's advertised count."""


def _ensure_scrape_meta(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS _scrape_meta (
               table_name TEXT PRIMARY KEY, layer_url TEXT,
               rows_fetched INTEGER DEFAULT 0,
               updated_at TEXT DEFAULT (datetime('now')))""")


def staging_name(table: str) -> str:
    return f"{table}__staging"


def promote_staging(conn: sqlite3.Connection, staging: str, table: str) -> None:
    """Atomically replace `table` with `staging` (drop + rename in one commit).

    This is the ONLY point a completed pull becomes current. Until it runs, the
    previous `table` is untouched, so a partial/failed pull can never be served.
    """
    _ensure_scrape_meta(conn)
    conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    conn.execute(f'ALTER TABLE "{staging}" RENAME TO "{table}"')
    conn.execute("DELETE FROM _scrape_meta WHERE table_name=?", (table,))
    conn.execute("UPDATE _scrape_meta SET table_name=? WHERE table_name=?",
                 (table, staging))
    conn.commit()


def load_features_staged(
    conn: sqlite3.Connection,
    table: str,
    esri_fields: list,
    make_features,
    with_geometry: bool,
    point_fn,
    layer_url: str = "",
    expected_count: Optional[int] = None,
    resume: bool = False,
    reconcile_tolerance: float = 0.0,
    batch_size: int = 500,
) -> int:
    """Pull ArcGIS features into a STAGING table, reconcile, then atomically
    promote it to `table` — so the live parcels table survives a failed pull.

    `make_features(start_offset)` builds the feature iterator; on resume it is
    called with the number of rows already staged so the pull continues where it
    left off. A mid-pagination failure propagates out of here BEFORE promotion,
    leaving `table` untouched and the partial data quarantined in staging.
    """
    _ensure_scrape_meta(conn)
    staging = staging_name(table)

    if not resume:
        conn.execute(f'DROP TABLE IF EXISTS "{staging}"')
        conn.execute("DELETE FROM _scrape_meta WHERE table_name=?", (staging,))
        conn.commit()

    mapping = create_feature_table(conn, staging, esri_fields, with_geometry)
    start = conn.execute(f'SELECT COUNT(*) FROM "{staging}"').fetchone()[0] if resume else 0

    # A failure inside insert_features (a raising feature generator) propagates
    # here and returns to the caller WITHOUT promoting — table stays current.
    insert_features(conn, staging, mapping, make_features(start), with_geometry,
                    point_fn, batch_size=batch_size, layer_url=layer_url)

    total = conn.execute(f'SELECT COUNT(*) FROM "{staging}"').fetchone()[0]

    if expected_count is not None and expected_count > 0:
        floor = expected_count * (1.0 - reconcile_tolerance)
        if total < floor:
            raise FeatureReconcileError(
                f"staged {total} features, expected ~{expected_count} "
                f"(floor {floor:.0f}); NOT promoting '{table}'")

    promote_staging(conn, staging, table)
    return total


def rows_fetched(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(
        "SELECT rows_fetched FROM _scrape_meta WHERE table_name=?", (table,)
    ).fetchone()
    return row[0] if row else 0


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def guess_owner_column(conn: sqlite3.Connection, table: str) -> Optional[str]:
    cols = table_columns(conn, table)
    for pattern in ("owner_name", "ownername", "owner", "mailto", "mail_to", "name"):
        for c in cols:
            if pattern in c.lower():
                return c
    return None


def stats(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '\\_%' ESCAPE '\\'"
        )
    ]
    return [(t, conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in tables]
