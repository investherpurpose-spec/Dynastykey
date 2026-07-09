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
