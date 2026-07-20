"""Tests for scraper.validate — the P0 validation gates.

Offline: builds a throwaway SQLite DB, so no network or real data is needed.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from scraper import validate


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE _scrape_meta (
               table_name TEXT PRIMARY KEY, layer_url TEXT,
               rows_fetched INTEGER, updated_at TEXT)"""
    )
    return conn


def _make_parcels(conn, rows):
    conn.execute('CREATE TABLE parcels (hcad_num TEXT, site_addr_1 TEXT)')
    conn.executemany('INSERT INTO parcels VALUES (?,?)', rows)
    conn.commit()


def _make_owners(conn, rows):
    conn.execute('CREATE TABLE owners (acct TEXT, mailto TEXT)')
    conn.executemany('INSERT INTO owners VALUES (?,?)', rows)
    conn.commit()


# ---- row_count ------------------------------------------------------------

def test_row_count_ok():
    conn = _conn()
    _make_parcels(conn, [(str(i), "ADDR") for i in range(10)])
    r = validate.check_row_count(conn, "parcels", min_expected=5, max_expected=20)
    assert r.status == "ok"
    assert r.observed == 10.0


def test_row_count_below_min_fails():
    conn = _conn()
    _make_parcels(conn, [(str(i), "ADDR") for i in range(3)])
    r = validate.check_row_count(conn, "parcels", min_expected=5)
    assert r.status == "fail"


def test_row_count_above_max_fails():
    conn = _conn()
    _make_parcels(conn, [(str(i), "ADDR") for i in range(30)])
    r = validate.check_row_count(conn, "parcels", min_expected=1, max_expected=10)
    assert r.status == "fail"


def test_row_count_warn_severity():
    conn = _conn()
    _make_parcels(conn, [(str(i), "ADDR") for i in range(2)])
    r = validate.check_row_count(conn, "parcels", min_expected=5, severity="warn")
    assert r.status == "warn"


# ---- required_nonnull -----------------------------------------------------

def test_required_nonnull_all_present():
    conn = _conn()
    _make_parcels(conn, [(str(i), "ADDR") for i in range(10)])
    r = validate.check_required_nonnull(conn, "parcels", "hcad_num", min_rate=1.0)
    assert r.status == "ok"
    assert r.observed == 1.0


def test_required_nonnull_counts_empty_strings_as_missing():
    conn = _conn()
    _make_parcels(conn, [("1", "A"), ("", "B"), ("  ", "C"), ("4", "D")])
    r = validate.check_required_nonnull(conn, "parcels", "hcad_num", min_rate=1.0)
    assert r.status == "fail"
    assert r.observed == 0.5  # 2 of 4 populated


def test_required_nonnull_below_rate():
    conn = _conn()
    _make_owners(conn, [("1", "X"), ("2", None), ("3", None), ("4", None)])
    r = validate.check_required_nonnull(conn, "owners", "mailto", min_rate=0.9,
                                        severity="warn")
    assert r.status == "warn"


def test_required_nonnull_empty_table_fails():
    conn = _conn()
    _make_parcels(conn, [])
    r = validate.check_required_nonnull(conn, "parcels", "hcad_num")
    assert r.status == "fail"


# ---- duplicate_rate -------------------------------------------------------

def test_duplicate_rate_none():
    conn = _conn()
    _make_owners(conn, [(str(i), "X") for i in range(10)])
    r = validate.check_duplicate_rate(conn, "owners", "acct", max_rate=0.0)
    assert r.status == "ok"
    assert r.observed == 0.0


def test_duplicate_rate_over_threshold():
    conn = _conn()
    # 4 rows, 2 distinct keys -> 50% duplicate
    _make_owners(conn, [("1", "A"), ("1", "B"), ("2", "C"), ("2", "D")])
    r = validate.check_duplicate_rate(conn, "owners", "acct", max_rate=0.1)
    assert r.status == "warn"
    assert r.observed == 0.5


def test_duplicate_rate_ignores_nulls():
    conn = _conn()
    _make_owners(conn, [("1", "A"), (None, "B"), ("2", "C")])
    r = validate.check_duplicate_rate(conn, "owners", "acct", max_rate=0.0)
    assert r.status == "ok"  # 2 non-null rows, 2 distinct


# ---- freshness ------------------------------------------------------------

def test_freshness_recent_ok():
    conn = _conn()
    _make_parcels(conn, [("1", "A")])
    ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn.execute("INSERT INTO _scrape_meta VALUES (?,?,?,?)", ("parcels", "", 1, ts))
    r = validate.check_freshness(conn, "parcels", max_age_hours=24)
    assert r.status == "ok"


def test_freshness_stale_warns():
    conn = _conn()
    _make_parcels(conn, [("1", "A")])
    ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    conn.execute("INSERT INTO _scrape_meta VALUES (?,?,?,?)", ("parcels", "", 1, ts))
    r = validate.check_freshness(conn, "parcels", max_age_hours=24)
    assert r.status == "warn"
    assert r.observed is not None and r.observed >= 47


def test_freshness_missing_provenance_warns():
    conn = _conn()
    _make_parcels(conn, [("1", "A")])
    r = validate.check_freshness(conn, "parcels", max_age_hours=24)
    assert r.status == "warn"
    assert "provenance" in r.detail


def test_freshness_naive_timestamp_handled():
    conn = _conn()
    _make_parcels(conn, [("1", "A")])
    # db.py stores datetime('now') as a naive UTC string
    ts = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO _scrape_meta VALUES (?,?,?,?)", ("parcels", "", 1, ts))
    r = validate.check_freshness(conn, "parcels", max_age_hours=24)
    assert r.status == "ok"


# ---- join_rate ------------------------------------------------------------

def test_join_rate_full_match():
    conn = _conn()
    _make_parcels(conn, [(str(i), "A") for i in range(5)])
    _make_owners(conn, [(str(i), "X") for i in range(5)])
    r = validate.check_join_rate(conn, "parcels", "hcad_num", "owners", "acct",
                                 min_rate=0.9)
    assert r.status == "ok"
    assert r.observed == 1.0


def test_join_rate_partial_match_warns():
    conn = _conn()
    _make_parcels(conn, [(str(i), "A") for i in range(10)])
    _make_owners(conn, [(str(i), "X") for i in range(5)])  # only half match
    r = validate.check_join_rate(conn, "parcels", "hcad_num", "owners", "acct",
                                 min_rate=0.9)
    assert r.status == "warn"
    assert r.observed == 0.5


# ---- validate_table + aggregation ----------------------------------------

def test_validate_table_missing_table_fails():
    conn = _conn()
    report = validate.validate_table(conn, {"table": "nope"})
    assert report.status == validate.FAIL
    assert report.checks[0].name == "table_exists"


def test_validate_table_all_pass():
    conn = _conn()
    _make_parcels(conn, [(str(i), "A") for i in range(10)])
    _make_owners(conn, [(str(i), "X") for i in range(10)])
    spec = {
        "table": "parcels",
        "row_count": {"min_expected": 5, "max_expected": 20},
        "required_nonnull": [{"column": "hcad_num", "min_rate": 1.0}],
        "duplicate_rate": [{"key_column": "hcad_num", "max_rate": 0.0}],
        "join_rate": [{"left_key": "hcad_num", "right_table": "owners",
                       "right_key": "acct", "min_rate": 0.9}],
    }
    report = validate.validate_table(conn, spec)
    assert report.status == validate.PASS
    assert report.reasons() == []


def test_validate_table_fail_dominates_warn():
    conn = _conn()
    _make_parcels(conn, [("", "A")] * 3)  # too few + required column empty
    spec = {
        "table": "parcels",
        "row_count": {"min_expected": 100},  # fail
        "duplicate_rate": [{"key_column": "hcad_num", "max_rate": 0.0}],  # warn
    }
    report = validate.validate_table(conn, spec)
    assert report.status == validate.FAIL


def test_overall_status_aggregation():
    ok = validate.ValidationReport("a", [validate.CheckResult("x", "ok", "")])
    warn = validate.ValidationReport("b", [validate.CheckResult("y", "warn", "")])
    fail = validate.ValidationReport("c", [validate.CheckResult("z", "fail", "")])
    assert validate.overall_status([ok]) == validate.PASS
    assert validate.overall_status([ok, warn]) == validate.PARTIAL
    assert validate.overall_status([ok, warn, fail]) == validate.FAIL


def test_report_to_dict_shape():
    conn = _conn()
    _make_owners(conn, [("1", "X")])
    report = validate.validate_table(conn, {
        "table": "owners", "row_count": {"min_expected": 1}})
    d = report.to_dict()
    assert d["table"] == "owners"
    assert d["status"] in (validate.PASS, validate.PARTIAL, validate.FAIL)
    assert isinstance(d["checks"], list) and d["checks"]
    assert set(d["checks"][0]) == {"name", "status", "detail", "observed", "threshold"}
