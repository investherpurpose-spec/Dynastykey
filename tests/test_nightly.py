"""Tests for the nightly spine runner.

Uses injected fake stages so no HCAD/GIS network is touched — exercises the
orchestration, run-state model, publication policy, last-good preservation, and
the overlap lock.
"""

import importlib.util
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    spec = importlib.util.spec_from_file_location(
        "nightly", os.path.join(_ROOT, "scripts", "nightly.py"))
    mod = importlib.util.module_from_spec(spec)
    # register before exec so dataclass forward-ref annotations resolve
    sys.modules["nightly"] = mod
    spec.loader.exec_module(mod)
    return mod


nightly = _load()


def _ok_stage(name, critical=True, rows=0):
    return nightly.Stage(name, critical, lambda ctx: nightly.StageOutcome(rows=rows))


def _publish_stage():
    return nightly.Stage("publish", True, nightly._stage_publish)


def _all_ok_steps():
    return [
        _ok_stage("preflight"),
        _ok_stage("hcad_load", rows=100),
        _ok_stage("gis_pull", rows=200),
        nightly.Stage("validation", True,
                      lambda ctx: nightly.StageOutcome(validation_status="PASS")),
        _publish_stage(),
    ]


def _read(p):
    return json.loads(p.read_text())


# ---- lock -----------------------------------------------------------------

def test_lock_acquire_and_release(tmp_path):
    lock = nightly.FileLock(tmp_path / "n.lock")
    assert lock.acquire() is True
    lock2 = nightly.FileLock(tmp_path / "n.lock")
    assert lock2.acquire() is False  # already held
    lock.release()
    assert lock2.acquire() is True    # freed
    lock2.release()


def test_lock_steals_stale_dead_pid(tmp_path):
    p = tmp_path / "n.lock"
    p.write_text(json.dumps({"pid": 999999999, "acquired_at": 0}))  # dead + ancient
    lock = nightly.FileLock(p)
    assert lock.acquire() is True
    lock.release()


def test_lock_steals_garbage(tmp_path):
    p = tmp_path / "n.lock"
    p.write_text("not json")
    assert nightly.FileLock(p).acquire() is True


def test_run_skipped_when_locked(tmp_path):
    (tmp_path / "nightly.lock").write_text(
        json.dumps({"pid": os.getpid(), "acquired_at": __import__("time").time()}))
    code = nightly.run_nightly("harris", tmp_path, steps=_all_ok_steps())
    assert code == nightly.EXIT_LOCKED


# ---- happy path -----------------------------------------------------------

def test_pass_publishes(tmp_path):
    code = nightly.run_nightly("harris", tmp_path, steps=_all_ok_steps(),
                               code_ver="abc123")
    assert code == nightly.EXIT_PASS
    att = _read(tmp_path / "latest_attempt.json")
    suc = _read(tmp_path / "latest_success.json")
    assert att["status"] == "PASS"
    assert att["completed"] is True and att["published"] is True
    assert att["code_version"] == "abc123"
    assert suc["run_id"] == att["run_id"]
    # per-run archive written
    archived = list((tmp_path / "runs").glob("*.json"))
    assert len(archived) == 1
    # stages carry durations
    assert all("duration_s" in s for s in att["stages"])


def test_run_id_unique(tmp_path):
    a = nightly.new_run_id()
    b = nightly.new_run_id()
    assert a != b


# ---- partial --------------------------------------------------------------

def test_validation_partial_still_publishes(tmp_path):
    steps = [
        _ok_stage("preflight"),
        _ok_stage("hcad_load", rows=100),
        _ok_stage("gis_pull", rows=200),
        nightly.Stage("validation", True,
                      lambda ctx: nightly.StageOutcome(validation_status="PARTIAL",
                                                       warnings=["low join rate"])),
        _publish_stage(),
    ]
    code = nightly.run_nightly("harris", tmp_path, steps=steps)
    assert code == nightly.EXIT_PARTIAL
    att = _read(tmp_path / "latest_attempt.json")
    assert att["status"] == "PARTIAL"
    assert att["published"] is True
    assert (tmp_path / "latest_success.json").exists()


def test_noncritical_failure_is_partial(tmp_path):
    def boom(ctx):
        raise RuntimeError("optional thing failed")
    steps = [
        _ok_stage("preflight"),
        _ok_stage("hcad_load", rows=100),
        nightly.Stage("enrichment", False, boom),  # non-critical
        _ok_stage("gis_pull", rows=200),
        nightly.Stage("validation", True,
                      lambda ctx: nightly.StageOutcome(validation_status="PASS")),
        _publish_stage(),
    ]
    code = nightly.run_nightly("harris", tmp_path, steps=steps)
    assert code == nightly.EXIT_PARTIAL
    att = _read(tmp_path / "latest_attempt.json")
    assert att["status"] == "PARTIAL"
    assert att["published"] is True  # non-critical failure still publishes


# ---- fail + last-good preservation ----------------------------------------

def test_critical_validation_fail_does_not_publish(tmp_path):
    steps = [
        _ok_stage("preflight"),
        _ok_stage("hcad_load", rows=100),
        _ok_stage("gis_pull", rows=200),
        nightly.Stage("validation", True,
                      lambda ctx: nightly.StageOutcome(validation_status="FAIL",
                                                       warnings=["count too low"])),
        _publish_stage(),
    ]
    code = nightly.run_nightly("harris", tmp_path, steps=steps)
    assert code == nightly.EXIT_FAIL
    att = _read(tmp_path / "latest_attempt.json")
    assert att["status"] == "FAIL"
    assert att["completed"] is True    # run completed...
    assert att["published"] is False   # ...but nothing was published
    assert not (tmp_path / "latest_success.json").exists()
    # publish stage must not have run
    assert [s["name"] for s in att["stages"]] == [
        "preflight", "hcad_load", "gis_pull", "validation"]


def test_critical_exception_aborts(tmp_path):
    def boom(ctx):
        raise RuntimeError("gis exploded")
    steps = [
        _ok_stage("preflight"),
        _ok_stage("hcad_load", rows=100),
        nightly.Stage("gis_pull", True, boom),
        _publish_stage(),
    ]
    code = nightly.run_nightly("harris", tmp_path, steps=steps)
    assert code == nightly.EXIT_FAIL
    att = _read(tmp_path / "latest_attempt.json")
    assert att["status"] == "FAIL"
    assert att["published"] is False
    assert att["stages"][-1]["name"] == "gis_pull"
    assert "gis exploded" in att["stages"][-1]["error"]


def test_failed_run_preserves_prior_success(tmp_path):
    # 1) a successful run publishes latest_success
    assert nightly.run_nightly("harris", tmp_path, steps=_all_ok_steps(),
                               code_ver="good") == nightly.EXIT_PASS
    good = _read(tmp_path / "latest_success.json")
    assert good["code_version"] == "good"

    # 2) a later failing run must NOT overwrite latest_success
    fail_steps = [
        _ok_stage("preflight"),
        nightly.Stage("hcad_load", True,
                      lambda ctx: (_ for _ in ()).throw(RuntimeError("bad load"))),
        _publish_stage(),
    ]
    assert nightly.run_nightly("harris", tmp_path, steps=fail_steps,
                               code_ver="bad") == nightly.EXIT_FAIL

    still = _read(tmp_path / "latest_success.json")
    assert still["code_version"] == "good"          # preserved
    attempt = _read(tmp_path / "latest_attempt.json")
    assert attempt["code_version"] == "bad"          # latest attempt is the failure
    assert attempt["status"] == "FAIL"


# ---- lock released so sequential runs work --------------------------------

def test_lock_released_between_runs(tmp_path):
    assert nightly.run_nightly("harris", tmp_path, steps=_all_ok_steps()) == 0
    assert nightly.run_nightly("harris", tmp_path, steps=_all_ok_steps()) == 0
    assert len(list((tmp_path / "runs").glob("*.json"))) == 2


# ---- structured log emitted -----------------------------------------------

def test_structured_log_written(tmp_path):
    nightly.run_nightly("harris", tmp_path, steps=_all_ok_steps())
    lines = [json.loads(l) for l in (tmp_path / "nightly.log").read_text().splitlines() if l.strip()]
    events = {l["event"] for l in lines}
    assert {"run_started", "stage_finished", "run_finished"} <= events


# ---- identity-spine stage data + prior-run regression ---------------------

def _spine_steps(metrics):
    """Steps where identity_join reports the given spine metrics dict."""
    return [
        _ok_stage("preflight"),
        _ok_stage("hcad_load", rows=100),
        _ok_stage("gis_pull", rows=200),
        nightly.Stage("identity_join", True, lambda ctx: nightly.StageOutcome(
            validation_status="PASS", data={"spine_metrics": metrics})),
        nightly.Stage("validation", True,
                      lambda ctx: nightly.StageOutcome(validation_status="PASS")),
        _publish_stage(),
    ]


def test_spine_metrics_recorded_in_manifest(tmp_path):
    metrics = {"unique_parcels": 1000, "unique_accounts": 1000, "join_rate": 0.95}
    nightly.run_nightly("harris", tmp_path, steps=_spine_steps(metrics))
    suc = _read(tmp_path / "latest_success.json")
    stage = next(s for s in suc["stages"] if s["name"] == "identity_join")
    assert stage["data"]["spine_metrics"]["unique_parcels"] == 1000


def test_prior_spine_metrics_passed_to_next_run(tmp_path):
    # run 1 publishes a baseline
    nightly.run_nightly("harris", tmp_path,
                        steps=_spine_steps({"unique_parcels": 1000,
                                            "unique_accounts": 1000, "join_rate": 0.95}))
    # run 2: a stage that asserts it received the prior baseline via ctx.params
    seen = {}

    def identity(ctx):
        seen["prior"] = ctx.params.get("prior_spine")
        return nightly.StageOutcome(validation_status="PASS",
                                    data={"spine_metrics": {"unique_parcels": 1000}})

    steps = [
        _ok_stage("preflight"),
        nightly.Stage("identity_join", True, identity),
        nightly.Stage("validation", True,
                      lambda ctx: nightly.StageOutcome(validation_status="PASS")),
        _publish_stage(),
    ]
    nightly.run_nightly("harris", tmp_path, steps=steps)
    assert seen["prior"] is not None
    assert seen["prior"]["unique_accounts"] == 1000


def test_real_spine_stage_fails_on_regression(tmp_path):
    # Build a DB and use the REAL identity stage to prove regression -> FAIL,
    # no publish, and prior success preserved.
    import sqlite3
    from scraper import db as dbmod

    db_path = tmp_path / "spine.db"
    conn = dbmod.connect(str(db_path))
    conn.execute('CREATE TABLE parcels (hcad_num TEXT, site_addr_1 TEXT)')
    conn.execute('CREATE TABLE owners (acct TEXT, mailto TEXT, mail_addr_1 TEXT)')
    conn.executemany('INSERT INTO parcels VALUES (?,?)', [(str(i), "ADDR") for i in range(100)])
    conn.executemany('INSERT INTO owners VALUES (?,?,?)',
                     [(str(i), "OWNER", "MAIL") for i in range(100)])
    conn.commit()
    conn.close()

    real_steps = [
        _ok_stage("preflight"),
        nightly.Stage("identity_join", True, nightly._stage_identity),
        nightly.Stage("validation", True,
                      lambda ctx: nightly.StageOutcome(validation_status="PASS")),
        _publish_stage(),
    ]
    # run 1: baseline (100 accounts), publishes
    code1 = nightly.run_nightly("harris", tmp_path, db_path=db_path, steps=real_steps)
    assert code1 == nightly.EXIT_PASS
    good = _read(tmp_path / "latest_success.json")

    # simulate a truncated owner file: drop owners to 10 accounts
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM owners WHERE CAST(acct AS INTEGER) >= 10")
    conn.commit()
    conn.close()

    # run 2: real spine stage should detect the material regression -> FAIL
    code2 = nightly.run_nightly("harris", tmp_path, db_path=db_path, steps=real_steps)
    assert code2 == nightly.EXIT_FAIL
    att = _read(tmp_path / "latest_attempt.json")
    assert att["status"] == "FAIL"
    assert att["published"] is False
    # prior good success preserved
    assert _read(tmp_path / "latest_success.json")["run_id"] == good["run_id"]
    # a notification was emitted
    assert (tmp_path / "notifications.log").exists()
