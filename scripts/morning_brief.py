#!/usr/bin/env python3
"""Aurora Morning Brief — deterministic CLI over existing platform data.

Answers "What should Tamika do today?" by rendering only systems that already
exist (PropertyProfile store, identity resolution, run-manifest events,
promotion/validation status). Invents nothing; every section that lacks a real
source says so explicitly.

Usage:
    python scripts/morning_brief.py                     # text brief
    python scripts/morning_brief.py --json              # machine-readable
    python scripts/morning_brief.py --db data/x.db --work-dir data/nightly
    python scripts/morning_brief.py --mark-seen         # persist "seen" state

Exit code is always 0 — the brief is a report, not a gate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from scraper import morning_brief as mb  # noqa: E402


def _connect_if_present(db_path: str):
    """Open the store read-only-ish if it exists; None otherwise (brief still runs)."""
    if not db_path or not os.path.exists(db_path):
        return None
    from scraper import db
    return db.connect(db_path)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Aurora Morning Brief (deterministic)")
    p.add_argument("--db", default="data/dynastykey.db", help="SQLite store path")
    p.add_argument("--work-dir", default="data/nightly",
                   help="nightly work-dir holding run manifests + brief state")
    p.add_argument("--county", default="harris")
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    p.add_argument("--no-color", action="store_true", help="disable ANSI color")
    p.add_argument("--review-cap", type=int, default=mb.DEFAULT_REVIEW_CAP,
                   help="max identity confirmations to show")
    p.add_argument("--mark-seen", action="store_true",
                   help="persist state so 'new since last brief' works next run")
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    conn = _connect_if_present(args.db)
    work_dir = args.work_dir
    prev_state = mb.load_state(work_dir)

    brief = mb.build_brief(conn=conn, work_dir=work_dir, county=args.county,
                           prev_state=prev_state, review_cap=args.review_cap)

    if args.json:
        print(json.dumps(brief.to_dict(), indent=2))
    else:
        color = (not args.no_color) and sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        print(mb.render_text(brief, color=color))

    if args.mark_seen:
        try:
            mb.save_state(work_dir, brief)
        except OSError as exc:
            print(f"warning: could not persist brief state: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
