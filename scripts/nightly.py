#!/usr/bin/env python3
"""Nightly Harris County spine runner (P0).

Orchestrates the production spine as an ordered set of classified stages:

    preflight -> HCAD load -> GIS pull -> identity join -> validation -> publish

Design points:
  - Each stage is CRITICAL or NON-CRITICAL. A critical failure aborts the run
    (FAIL, nothing published); a non-critical failure degrades it to PARTIAL.
  - A unique run_id and the code version (git commit) are recorded, plus per-
    stage durations, row counts, warnings, and errors.
  - "Run completion" (the orchestration finished) is tracked separately from
    "successful publication" (outputs were promoted to latest_success).
  - The latest *attempted* run and the latest *successfully published* run are
    preserved as separate artifacts, so a failed run never clobbers the last
    good published manifest.
  - Overlapping runs are prevented with an atomic, stale-aware file lock.
  - This runner does NOT reimplement importer-level last-good preservation
    (hcad_bulk / arcgis already stage+swap); it composes those guarantees.

Exit codes: 0 PASS · 1 PARTIAL · 2 FAIL · 3 already-running (lock held).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# Make the repo importable when run as `python scripts/nightly.py`.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

EXIT_PASS = 0
EXIT_PARTIAL = 1
EXIT_FAIL = 2
EXIT_LOCKED = 3

PASS, PARTIAL, FAIL = "PASS", "PARTIAL", "FAIL"
_STATUS_EXIT = {PASS: EXIT_PASS, PARTIAL: EXIT_PARTIAL, FAIL: EXIT_FAIL}

LOCK_TTL_SECONDS = 6 * 3600  # a lock older than this is considered stale


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_run_id(now: Optional[datetime] = None) -> str:
    now = now or _now()
    return now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def code_version() -> str:
    """Best-effort git commit of the running code; 'unknown' if unavailable."""
    env = os.environ.get("DYNASTYKEY_CODE_VERSION")
    if env:
        return env
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=_REPO_ROOT, capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


# --------------------------------------------------------------------------
# Lock
# --------------------------------------------------------------------------

class FileLock:
    """Atomic, stale-aware lock so two nightly runs never overlap.

    Uses O_CREAT|O_EXCL for the atomic create. A lock whose owner process is
    dead, or that is older than LOCK_TTL_SECONDS, is treated as stale and stolen.
    """

    def __init__(self, path, ttl_seconds: int = LOCK_TTL_SECONDS):
        self.path = Path(path)
        self.ttl = ttl_seconds
        self._held = False

    def _pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists but not ours
        except OSError:
            return False
        return True

    def _is_stale(self) -> bool:
        try:
            info = json.loads(self.path.read_text())
        except (OSError, ValueError):
            # unreadable/garbage lock -> treat as stale
            return True
        pid = int(info.get("pid", -1))
        ts = info.get("acquired_at", 0)
        age = time.time() - float(ts or 0)
        if not self._pid_alive(pid):
            return True
        if age > self.ttl:
            return True
        return False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"pid": os.getpid(), "acquired_at": time.time()})
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as fh:
                fh.write(payload)
            self._held = True
            return True
        except FileExistsError:
            if self._is_stale():
                try:
                    self.path.unlink()
                except OSError:
                    return False
                return self.acquire()
            return False

    def release(self) -> None:
        if self._held:
            try:
                self.path.unlink()
            except OSError:
                pass
            self._held = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


# --------------------------------------------------------------------------
# Run-state model
# --------------------------------------------------------------------------

@dataclass
class StageOutcome:
    """What a stage function returns on success."""
    rows: int = 0
    warnings: list = field(default_factory=list)
    detail: str = ""
    # validation stages report PASS/PARTIAL/FAIL; a FAIL on a critical stage
    # aborts the run just like an exception would.
    validation_status: Optional[str] = None


@dataclass
class Stage:
    name: str
    critical: bool
    fn: Callable[["NightlyContext"], StageOutcome]


@dataclass
class StageResult:
    name: str
    critical: bool
    status: str            # ok | warn | fail | skipped
    duration_s: float
    rows: int = 0
    warnings: list = field(default_factory=list)
    error: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name, "critical": self.critical, "status": self.status,
            "duration_s": round(self.duration_s, 4), "rows": self.rows,
            "warnings": self.warnings, "error": self.error, "detail": self.detail,
        }


@dataclass
class RunManifest:
    run_id: str
    county_id: str
    code_version: str
    started_at: str
    ended_at: str = ""
    duration_s: float = 0.0
    status: str = FAIL
    completed: bool = False     # did orchestration reach the end
    published: bool = False     # were outputs promoted to latest_success
    stages: list = field(default_factory=list)   # list[StageResult]
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id, "county_id": self.county_id,
            "code_version": self.code_version, "started_at": self.started_at,
            "ended_at": self.ended_at, "duration_s": round(self.duration_s, 4),
            "status": self.status, "completed": self.completed,
            "published": self.published,
            "stages": [s.to_dict() for s in self.stages],
            "reasons": self.reasons,
        }


class NightlyContext:
    def __init__(self, county_id: str, work_dir: Path, db_path: Path,
                 exports_dir: Path, params: dict, log):
        self.county_id = county_id
        self.work_dir = Path(work_dir)
        self.db_path = Path(db_path)
        self.exports_dir = Path(exports_dir)
        self.params = params or {}
        self.log = log
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            from scraper import db
            self._conn = db.connect(str(self.db_path))
        return self._conn


# --------------------------------------------------------------------------
# Artifact IO  (structured JSON logs + durable manifests come from monitoring
# in the next backlog item; this keeps a minimal, self-contained writer.)
# --------------------------------------------------------------------------

def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    tmp.replace(path)


def _make_logger(work_dir: Path):
    log_path = Path(work_dir) / "nightly.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(event: str, **fields):
        rec = {"ts": _now().isoformat(), "event": event}
        rec.update(fields)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    return log


# --------------------------------------------------------------------------
# Default spine stages (wired to the real importers; injected fakes in tests).
# The identity-join stage is inserted by the identity-spine backlog item.
# --------------------------------------------------------------------------

def _stage_preflight(ctx: NightlyContext) -> StageOutcome:
    ctx.work_dir.mkdir(parents=True, exist_ok=True)
    ctx.exports_dir.mkdir(parents=True, exist_ok=True)
    # a cheap writability probe
    probe = ctx.work_dir / ".preflight"
    probe.write_text("ok")
    probe.unlink()
    return StageOutcome(detail="work/exports dirs writable")


def _stage_hcad(ctx: NightlyContext) -> StageOutcome:
    from scraper import hcad_bulk
    zip_path = ctx.params.get("hcad_zip")
    if not zip_path:
        year = ctx.params.get("hcad_year")
        if not year:
            raise RuntimeError("no hcad_zip or hcad_year configured")
        zip_path = hcad_bulk.download_bulk_zip(year)
    m = hcad_bulk.load_owners(ctx.conn, zip_path, resume=ctx.params.get("resume", False))
    warnings = []
    if m.expected_missing:
        warnings.append(f"expected columns absent: {m.expected_missing}")
    if not m.reconciled:
        raise RuntimeError("HCAD load did not reconcile")
    return StageOutcome(rows=m.rows_inserted, warnings=warnings,
                        detail=f"{m.duplicates} dupes, {m.quarantined} quarantined")


def _stage_gis(ctx: NightlyContext) -> StageOutcome:
    from scraper import arcgis, db
    layer = ctx.params.get("gis_layer") or arcgis.HARRIS_PARCELS_LAYER
    info = arcgis.get_layer_info(layer)
    table = ctx.params.get("gis_table", "parcels")
    fields = info.get("fields") or []
    mapping = db.create_feature_table(ctx.conn, table, fields,
                                      ctx.params.get("geometry", False))
    feats = arcgis.iter_features(layer, include_geometry=ctx.params.get("geometry", False),
                                 limit=ctx.params.get("gis_limit"))
    total = db.insert_features(ctx.conn, table, mapping, feats,
                               ctx.params.get("geometry", False), arcgis.point_of,
                               layer_url=layer)
    return StageOutcome(rows=total, detail=f"parcels layer -> {table}")


def _stage_validation(ctx: NightlyContext) -> StageOutcome:
    from scraper import validate
    reports = validate.validate_many(ctx.conn, validate.HARRIS_SPINE_SPECS)
    overall = validate.overall_status(reports)
    warnings = [r for rep in reports for r in rep.reasons()]
    return StageOutcome(validation_status=overall, warnings=warnings,
                        detail=f"spine validation {overall}")


def _stage_publish(ctx: NightlyContext) -> StageOutcome:
    marker = ctx.exports_dir / ctx.county_id / "spine_ready.json"
    _write_json(marker, {"published_at": _now().isoformat(), "county": ctx.county_id})
    return StageOutcome(detail=f"published marker {marker.name}")


def default_steps() -> list:
    return [
        Stage("preflight", True, _stage_preflight),
        Stage("hcad_load", True, _stage_hcad),
        Stage("gis_pull", True, _stage_gis),
        Stage("validation", True, _stage_validation),
        Stage("publish", True, _stage_publish),
    ]


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def _classify(outcome: StageOutcome) -> str:
    if outcome.validation_status == FAIL:
        return "fail"
    if outcome.validation_status == PARTIAL or outcome.warnings:
        return "warn"
    return "ok"


def run_nightly(county_id: str, work_dir, *, db_path=None, exports_dir=None,
                steps: Optional[list] = None, params: Optional[dict] = None,
                now: Optional[datetime] = None, code_ver: Optional[str] = None) -> int:
    work_dir = Path(work_dir)
    db_path = Path(db_path) if db_path else work_dir / "dynastykey.db"
    exports_dir = Path(exports_dir) if exports_dir else work_dir / "exports"
    log = _make_logger(work_dir)

    lock = FileLock(work_dir / "nightly.lock")
    if not lock.acquire():
        log("run_skipped_locked", county=county_id)
        return EXIT_LOCKED

    try:
        start = now or _now()
        run_id = new_run_id(start)
        manifest = RunManifest(
            run_id=run_id, county_id=county_id,
            code_version=code_ver or code_version(),
            started_at=start.isoformat())
        ctx = NightlyContext(county_id, work_dir, db_path, exports_dir, params or {}, log)
        log("run_started", run_id=run_id, county=county_id,
            code_version=manifest.code_version)

        steps = steps if steps is not None else default_steps()
        aborted = False
        degraded = False
        published_stage_ok = False

        for stage in steps:
            t0 = time.monotonic()
            try:
                outcome = stage.fn(ctx)
                status = _classify(outcome)
                sr = StageResult(stage.name, stage.critical, status,
                                 time.monotonic() - t0, rows=outcome.rows,
                                 warnings=list(outcome.warnings), detail=outcome.detail)
                manifest.stages.append(sr)
                log("stage_finished", run_id=run_id, stage=stage.name,
                    status=status, rows=outcome.rows, duration_s=round(sr.duration_s, 4))
                if status == "fail" and stage.critical:
                    aborted = True
                    manifest.reasons.append(f"{stage.name}: critical FAIL ({outcome.detail})")
                    break
                if status == "warn":
                    degraded = True
                    if stage.name == "validation" and outcome.validation_status == PARTIAL:
                        manifest.reasons.append("validation PARTIAL")
                if stage.name == "publish":
                    published_stage_ok = True
            except Exception as exc:  # a raising stage
                sr = StageResult(stage.name, stage.critical, "fail",
                                 time.monotonic() - t0, error=str(exc))
                manifest.stages.append(sr)
                log("stage_error", run_id=run_id, stage=stage.name, error=str(exc))
                manifest.reasons.append(f"{stage.name}: {exc}")
                if stage.critical:
                    aborted = True
                    break
                degraded = True

        manifest.completed = True  # orchestration reached its end (pass or fail)

        if aborted:
            manifest.status = FAIL
        elif degraded:
            manifest.status = PARTIAL
        else:
            manifest.status = PASS

        # Publication: only on PASS/PARTIAL and only if the publish stage ran ok.
        manifest.published = manifest.status in (PASS, PARTIAL) and published_stage_ok

        end = _now()
        manifest.ended_at = end.isoformat()
        manifest.duration_s = (end - start).total_seconds()

        # latest ATTEMPTED is always written; latest SUCCESS only when published,
        # so a failed run never clobbers the last good published manifest.
        _write_json(work_dir / "runs" / f"{run_id}.json", manifest.to_dict())
        _write_json(work_dir / "latest_attempt.json", manifest.to_dict())
        if manifest.published:
            _write_json(work_dir / "latest_success.json", manifest.to_dict())

        log("run_finished", run_id=run_id, status=manifest.status,
            completed=manifest.completed, published=manifest.published,
            duration_s=round(manifest.duration_s, 4))
        return _STATUS_EXIT[manifest.status]
    finally:
        lock.release()


def _build_arg_parser():
    import argparse
    p = argparse.ArgumentParser(description="Nightly Harris County spine runner")
    p.add_argument("--county", default="harris")
    p.add_argument("--work-dir", default="data/nightly")
    p.add_argument("--db", default=None)
    p.add_argument("--exports-dir", default=None)
    p.add_argument("--hcad-zip", default=None, help="path to Real_acct_owner zip")
    p.add_argument("--hcad-year", type=int, default=None)
    p.add_argument("--gis-layer", default=None)
    p.add_argument("--gis-limit", type=int, default=None)
    p.add_argument("--geometry", action="store_true")
    p.add_argument("--resume", action="store_true")
    return p


def main(argv=None) -> int:
    args = _build_arg_parser().parse_args(argv)
    params = {
        "hcad_zip": args.hcad_zip, "hcad_year": args.hcad_year,
        "gis_layer": args.gis_layer, "gis_limit": args.gis_limit,
        "geometry": args.geometry, "resume": args.resume,
    }
    return run_nightly(args.county, args.work_dir, db_path=args.db,
                       exports_dir=args.exports_dir, params=params)


if __name__ == "__main__":
    sys.exit(main())
