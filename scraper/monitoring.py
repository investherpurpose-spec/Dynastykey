"""Durable run monitoring for the nightly pipeline.

Operates entirely on the JSON artifacts the nightly runner already produces —
no external observability platform, service, or new framework. Provides:

  - make_logger(): structured JSON-lines logging (one object per event)
  - emit_alert(): append a concise alert record on non-PASS runs
  - prune_runs(): retention for the per-run manifest archive
  - run_history() / latest_attempt() / latest_success(): manifest readers
  - summarize_health() / format_health(): a health view over recent runs,
    surfacing latest attempt vs latest successful publication and consecutive
    failure streaks

Artifact layout (under a run work_dir):
    nightly.log            append-only JSON-lines event log
    notifications.log      append-only JSON-lines alert log
    runs/<run_id>.json     durable per-run manifest
    latest_attempt.json    the most recent run (any status)
    latest_success.json    the most recent successfully published run
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

RUNS_DIR = "runs"
LOG_FILE = "nightly.log"
NOTIFICATIONS_FILE = "notifications.log"
LATEST_ATTEMPT = "latest_attempt.json"
LATEST_SUCCESS = "latest_success.json"
DEFAULT_KEEP = 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_json_line(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def make_logger(work_dir):
    """Return a log(event, **fields) callable writing JSON lines to nightly.log."""
    log_path = Path(work_dir) / LOG_FILE

    def log(event: str, **fields):
        rec = {"ts": _now_iso(), "event": event}
        rec.update(fields)
        _append_json_line(log_path, rec)
    return log


def failed_stages(manifest: dict) -> list:
    return [s["name"] for s in manifest.get("stages", []) if s.get("status") == "fail"]


def emit_alert(work_dir, manifest: dict) -> dict:
    """Append a concise alert record for a run and return it.

    Intended for non-PASS runs, but records whatever it is given (the caller
    decides when to alert). Deliberately minimal: status, reasons, failed
    stages — no external delivery, just a durable local notification log.
    """
    record = {
        "ts": _now_iso(),
        "run_id": manifest.get("run_id"),
        "county": manifest.get("county_id"),
        "status": manifest.get("status"),
        "published": manifest.get("published"),
        "failed_stages": failed_stages(manifest),
        "reasons": manifest.get("reasons", []),
    }
    _append_json_line(Path(work_dir) / NOTIFICATIONS_FILE, record)
    return record


def prune_runs(work_dir, keep: int = DEFAULT_KEEP) -> int:
    """Keep the newest `keep` per-run manifests; delete older. Returns #deleted.

    run_id is timestamp-prefixed, so lexical sort is chronological.
    """
    runs_dir = Path(work_dir) / RUNS_DIR
    if not runs_dir.is_dir():
        return 0
    files = sorted(runs_dir.glob("*.json"), key=lambda p: p.name)
    excess = files[:-keep] if keep > 0 else files
    deleted = 0
    for f in excess:
        try:
            f.unlink()
            deleted += 1
        except OSError:
            pass
    return deleted


def _load(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def latest_attempt(work_dir) -> Optional[dict]:
    return _load(Path(work_dir) / LATEST_ATTEMPT)


def latest_success(work_dir) -> Optional[dict]:
    return _load(Path(work_dir) / LATEST_SUCCESS)


def run_history(work_dir, limit: int = 10) -> list:
    """Return recent run manifests, newest first."""
    runs_dir = Path(work_dir) / RUNS_DIR
    if not runs_dir.is_dir():
        return []
    files = sorted(runs_dir.glob("*.json"), key=lambda p: p.name, reverse=True)
    out = []
    for f in files[:limit]:
        m = _load(f)
        if m:
            out.append(m)
    return out


def consecutive_failures(work_dir, limit: int = 60) -> int:
    """Count runs since (and not including) the most recent published success."""
    n = 0
    for m in run_history(work_dir, limit=limit):
        if m.get("published"):
            break
        if m.get("status") in ("FAIL", "PARTIAL") and not m.get("published"):
            n += 1
        elif m.get("status") == "FAIL":
            n += 1
        else:
            break
    return n


def _brief(m: Optional[dict]) -> Optional[dict]:
    if not m:
        return None
    return {
        "run_id": m.get("run_id"), "status": m.get("status"),
        "ended_at": m.get("ended_at"), "published": m.get("published"),
        "code_version": m.get("code_version"),
    }


def summarize_health(work_dir) -> dict:
    att = latest_attempt(work_dir)
    suc = latest_success(work_dir)
    recent = run_history(work_dir, limit=10)
    return {
        "latest_attempt": _brief(att),
        "latest_success": _brief(suc),
        "consecutive_failures": consecutive_failures(work_dir),
        "recent": [_brief(m) for m in recent],
        "healthy": bool(att and att.get("status") == "PASS"),
    }


def format_health(summary: dict) -> str:
    lines = []
    att = summary.get("latest_attempt")
    suc = summary.get("latest_success")
    lines.append("Nightly health")
    lines.append(f"  latest attempt: "
                 + (f"{att['run_id']} {att['status']} (published={att['published']})"
                    if att else "none"))
    lines.append(f"  latest success: "
                 + (f"{suc['run_id']} {suc['status']} @ {suc['ended_at']}"
                    if suc else "none"))
    lines.append(f"  consecutive failures since last success: "
                 f"{summary.get('consecutive_failures', 0)}")
    if summary.get("recent"):
        lines.append("  recent:")
        for m in summary["recent"]:
            lines.append(f"    {m['run_id']}  {m['status']:<7} "
                         f"published={m['published']}")
    return "\n".join(lines)
