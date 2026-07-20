"""Tests for scraper.monitoring — durable JSON monitoring over run manifests."""

import json

from scraper import monitoring as mon


def _manifest(run_id, status="PASS", published=True, stages=None, reasons=None,
              county="harris", code="abc"):
    return {
        "run_id": run_id, "county_id": county, "status": status,
        "published": published, "code_version": code, "ended_at": "2026-01-01T00:00:00Z",
        "stages": stages or [], "reasons": reasons or [],
    }


def _write_run(work_dir, m):
    d = work_dir / "runs"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{m['run_id']}.json").write_text(json.dumps(m))


# ---- logger ---------------------------------------------------------------

def test_make_logger_writes_json_lines(tmp_path):
    log = mon.make_logger(tmp_path)
    log("hello", run_id="r1", n=3)
    log("world", ok=True)
    lines = [json.loads(l) for l in (tmp_path / "nightly.log").read_text().splitlines()]
    assert lines[0]["event"] == "hello" and lines[0]["n"] == 3 and "ts" in lines[0]
    assert lines[1]["event"] == "world" and lines[1]["ok"] is True


# ---- alerts ---------------------------------------------------------------

def test_emit_alert_records_failed_stages(tmp_path):
    m = _manifest("r1", status="FAIL", published=False,
                  stages=[{"name": "gis_pull", "status": "fail"},
                          {"name": "hcad_load", "status": "ok"}],
                  reasons=["gis_pull: boom"])
    rec = mon.emit_alert(tmp_path, m)
    assert rec["status"] == "FAIL"
    assert rec["failed_stages"] == ["gis_pull"]
    lines = (tmp_path / "notifications.log").read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["run_id"] == "r1"


def test_emit_alert_appends(tmp_path):
    mon.emit_alert(tmp_path, _manifest("r1", status="FAIL", published=False))
    mon.emit_alert(tmp_path, _manifest("r2", status="PARTIAL", published=True))
    lines = (tmp_path / "notifications.log").read_text().splitlines()
    assert len(lines) == 2


# ---- retention ------------------------------------------------------------

def test_prune_keeps_newest(tmp_path):
    for i in range(10):
        _write_run(tmp_path, _manifest(f"2026010{i}T000000Z-{i:08x}"))
    deleted = mon.prune_runs(tmp_path, keep=4)
    assert deleted == 6
    remaining = sorted(p.name for p in (tmp_path / "runs").glob("*.json"))
    assert len(remaining) == 4
    # newest kept (highest run_id lexically)
    assert remaining[-1].startswith("20260109")


def test_prune_noop_when_under_keep(tmp_path):
    _write_run(tmp_path, _manifest("20260101T000000Z-00000001"))
    assert mon.prune_runs(tmp_path, keep=10) == 0


def test_prune_missing_dir(tmp_path):
    assert mon.prune_runs(tmp_path / "nope", keep=5) == 0


# ---- history + health -----------------------------------------------------

def test_run_history_newest_first(tmp_path):
    for i in range(3):
        _write_run(tmp_path, _manifest(f"2026010{i}T000000Z-{i:08x}", status="PASS"))
    hist = mon.run_history(tmp_path, limit=2)
    assert len(hist) == 2
    assert hist[0]["run_id"] > hist[1]["run_id"]  # newest first


def test_summarize_health_reports_attempt_and_success(tmp_path):
    (tmp_path / "latest_attempt.json").write_text(
        json.dumps(_manifest("rA", status="FAIL", published=False)))
    (tmp_path / "latest_success.json").write_text(
        json.dumps(_manifest("rS", status="PASS", published=True)))
    _write_run(tmp_path, _manifest("20260101T000000Z-0001", status="PASS", published=True))
    _write_run(tmp_path, _manifest("20260102T000000Z-0002", status="FAIL", published=False))
    s = mon.summarize_health(tmp_path)
    assert s["latest_attempt"]["run_id"] == "rA"
    assert s["latest_attempt"]["status"] == "FAIL"
    assert s["latest_success"]["run_id"] == "rS"
    assert s["healthy"] is False
    text = mon.format_health(s)
    assert "latest attempt" in text and "latest success" in text


def test_consecutive_failures_counts_until_success(tmp_path):
    _write_run(tmp_path, _manifest("20260101T000000Z-0001", status="PASS", published=True))
    _write_run(tmp_path, _manifest("20260102T000000Z-0002", status="FAIL", published=False))
    _write_run(tmp_path, _manifest("20260103T000000Z-0003", status="FAIL", published=False))
    assert mon.consecutive_failures(tmp_path) == 2


def test_healthy_when_latest_pass(tmp_path):
    (tmp_path / "latest_attempt.json").write_text(
        json.dumps(_manifest("rA", status="PASS", published=True)))
    assert mon.summarize_health(tmp_path)["healthy"] is True
