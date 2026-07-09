#!/usr/bin/env python3
"""Harris County ArcGIS scraper CLI.

Typical workflow:
    python main.py discover https://www.gis.hctx.net/arcgishcpid/rest/services/HCAD/Parcels/FeatureServer/0
    python main.py pull-parcels                       # bulk-pull all parcels into SQLite
    python main.py load-owners --year 2025            # load HCAD bulk owner names
    python main.py match --name "JOHN SMITH"          # match names against the DB
    python main.py match --names-file leads.txt --csv matches.csv
    python main.py claude-match --names-file leads.txt   # LLM-assisted matching
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys

from scraper import arcgis, db, hcad_bulk, names

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def cmd_discover(args):
    info = arcgis.get_service_info(args.url)
    if "fields" in info:  # a layer
        print(f"Layer: {info.get('name')}")
        print(f"  geometry: {info.get('geometryType')}")
        print(f"  maxRecordCount: {info.get('maxRecordCount')}")
        adv = info.get("advancedQueryCapabilities") or {}
        print(f"  supportsPagination: {adv.get('supportsPagination')}")
        try:
            print(f"  feature count: {arcgis.count_features(args.url)}")
        except Exception as exc:  # count is best-effort
            print(f"  feature count: unavailable ({exc})")
        print("  fields:")
        for f in info["fields"]:
            print(f"    {f['name']:<30} {f.get('type','')}  {f.get('alias','')}")
    else:  # a service/folder
        for key in ("folders", "services", "layers", "tables"):
            if info.get(key):
                print(f"{key}:")
                for item in info[key]:
                    print(f"  {item if isinstance(item, str) else item.get('name')}")


def cmd_pull(args):
    conn = db.connect(args.db)
    info = arcgis.get_layer_info(args.url)
    table = args.table or db.sanitize(info.get("name") or "features")
    fields = info.get("fields") or []
    mapping = db.create_feature_table(conn, table, fields, args.geometry)

    resume_offset = 0
    if args.resume:
        resume_offset = db.rows_fetched(conn, table)
        print(f"resuming '{table}' from offset {resume_offset}")
    elif conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]:
        print(f"table '{table}' already has rows; use --resume to continue or pick a new --table")
        sys.exit(1)

    try:
        total_available = arcgis.count_features(args.url, args.where)
        print(f"{total_available} features available in layer '{info.get('name')}'")
    except Exception:
        pass

    feats = arcgis.iter_features(
        args.url,
        where=args.where,
        include_geometry=args.geometry,
        start_offset=resume_offset,
        limit=args.limit,
    )
    total = db.insert_features(
        conn, table, mapping, feats, args.geometry, arcgis.point_of, layer_url=args.url
    )
    print(f"done: {total} features stored in table '{table}' ({args.db})")


def cmd_pull_parcels(args):
    args.url = args.url or arcgis.HARRIS_PARCELS_LAYER
    args.table = args.table or "parcels"
    cmd_pull(args)


def cmd_load_owners(args):
    conn = db.connect(args.db)
    zip_path = args.zip or hcad_bulk.download_bulk_zip(args.year)
    total = hcad_bulk.load_owners(conn, zip_path, limit=args.limit)
    print(f"done: {total} owner rows loaded into table '{hcad_bulk.OWNERS_TABLE}'")


def _resolve_table_field(conn, args):
    table = args.table or hcad_bulk.OWNERS_TABLE
    field = args.field or db.guess_owner_column(conn, table)
    if not field:
        print(f"could not guess a name column in '{table}'; pass --field", file=sys.stderr)
        sys.exit(1)
    return table, field


def _read_queries(args) -> list[str]:
    if args.name:
        return [args.name]
    with open(args.names_file, encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip()]


def cmd_match(args):
    conn = db.connect(args.db)
    table, field = _resolve_table_field(conn, args)
    matches = names.match_many(conn, _read_queries(args), table, field, args.threshold)

    if args.csv:
        keys = sorted({k for m in matches for k in m.row}) if matches else []
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["query", "matched_value", "score", "method", *keys])
            for m in matches:
                writer.writerow([m.query, m.matched_value, m.score, m.method]
                                + [m.row.get(k) for k in keys])
        print(f"{len(matches)} matches written to {args.csv}")
    else:
        for m in matches:
            print(f"{m.score:>5}  [{m.method}]  {m.query!r} -> {m.matched_value!r}")
        print(f"({len(matches)} matches, table={table}, field={field})")


def cmd_claude_match(args):
    from scraper.claude_match import claude_match_name

    conn = db.connect(args.db)
    table, field = _resolve_table_field(conn, args)
    all_matches = []
    for q in _read_queries(args):
        results = claude_match_name(conn, q, table, field, model=args.model)
        all_matches.extend(results)
        for r in results:
            print(f"[{r['confidence']}] {q!r} -> {r['matched_value']!r}  ({r['reason']})")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(all_matches, fh, indent=2, default=str)
        print(f"wrote {len(all_matches)} matches to {args.json_out}")


def cmd_stats(args):
    conn = db.connect(args.db)
    for table, count in db.stats(conn):
        print(f"{table:<30} {count:>10} rows")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=db.DEFAULT_DB, help="SQLite database path")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("discover", help="inspect an ArcGIS service/layer URL (folders, layers, fields)")
    d.add_argument("url")
    d.set_defaults(func=cmd_discover)

    def add_pull_args(sp, url_required):
        if url_required:
            sp.add_argument("url")
        else:
            sp.add_argument("--url", default=None, help="override the default layer URL")
        sp.add_argument("--table", default=None)
        sp.add_argument("--where", default="1=1")
        sp.add_argument("--geometry", action="store_true", help="also store point/centroid + raw geometry")
        sp.add_argument("--limit", type=int, default=None, help="stop after N features (for testing)")
        sp.add_argument("--resume", action="store_true", help="continue an interrupted pull")

    g = sub.add_parser("pull", help="bulk-pull any ArcGIS layer into SQLite")
    add_pull_args(g, url_required=True)
    g.set_defaults(func=cmd_pull)

    pp = sub.add_parser("pull-parcels", help="bulk-pull the Harris County (HCAD) parcels layer")
    add_pull_args(pp, url_required=False)
    pp.set_defaults(func=cmd_pull_parcels)

    lo = sub.add_parser("load-owners", help="download + load HCAD bulk owner data (real_acct)")
    lo.add_argument("--year", type=int, default=2025)
    lo.add_argument("--zip", default=None, help="use an already-downloaded Real_acct_owner.zip")
    lo.add_argument("--limit", type=int, default=None)
    lo.set_defaults(func=cmd_load_owners)

    def add_match_args(sp):
        grp = sp.add_mutually_exclusive_group(required=True)
        grp.add_argument("--name", help="a single name to look up")
        grp.add_argument("--names-file", help="text file, one name per line")
        sp.add_argument("--table", default=None, help="table to search (default: owners)")
        sp.add_argument("--field", default=None, help="column with the owner name (default: auto)")

    m = sub.add_parser("match", help="match names against the database (local fuzzy matching)")
    add_match_args(m)
    m.add_argument("--threshold", type=float, default=0.82)
    m.add_argument("--csv", default=None, help="write matches to a CSV file")
    m.set_defaults(func=cmd_match)

    cm = sub.add_parser("claude-match", help="LLM-assisted matching for messy names (needs ANTHROPIC_API_KEY)")
    add_match_args(cm)
    cm.add_argument("--model", default="claude-opus-4-8")
    cm.add_argument("--json-out", default=None)
    cm.set_defaults(func=cmd_claude_match)

    st = sub.add_parser("stats", help="row counts per table")
    st.set_defaults(func=cmd_stats)
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.func(args)
