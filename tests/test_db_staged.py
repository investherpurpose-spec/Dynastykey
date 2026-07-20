"""Proof tests for staged GIS load — partial output cannot become current.

These exercise db.load_features_staged / promote_staging with a controllable
feature generator (no network): success promotes, a mid-pagination failure
leaves the live table untouched, resume continues, and reconciliation shortfall
blocks promotion.
"""

import sqlite3

import pytest

from scraper import db


FIELDS = [
    {"name": "OBJECTID", "type": "esriFieldTypeOID"},
    {"name": "HCAD_NUM", "type": "esriFieldTypeString"},
    {"name": "SITE_ADDR_1", "type": "esriFieldTypeString"},
]


def _conn(tmp_path):
    return db.connect(str(tmp_path / "gis.db"))


def _feat(i):
    return {"attributes": {"OBJECTID": i, "HCAD_NUM": str(i),
                           "SITE_ADDR_1": f"{i} MAIN ST"}}


def _table_exists(conn, t):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def _count(conn, t):
    return conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]


# ---- success promotes -----------------------------------------------------

def test_staged_load_promotes(tmp_path):
    conn = _conn(tmp_path)

    def make(start):
        return (_feat(i) for i in range(start, 10))

    total = db.load_features_staged(conn, "parcels", FIELDS, make, False,
                                    lambda f: (None, None), expected_count=10)
    assert total == 10
    assert _count(conn, "parcels") == 10
    # staging is gone (renamed into place)
    assert not _table_exists(conn, "parcels__staging")


# ---- mid-pagination failure preserves the live table ----------------------

def test_midpull_failure_leaves_previous_table_current(tmp_path):
    conn = _conn(tmp_path)

    # 1) an initial good pull publishes parcels (2 rows)
    db.load_features_staged(conn, "parcels", FIELDS,
                            lambda s: (_feat(i) for i in range(s, 2)),
                            False, lambda f: (None, None), expected_count=2)
    assert _count(conn, "parcels") == 2

    # 2) a second pull that raises after 3 features must NOT touch parcels
    def exploding(start):
        for i in range(start, 10):
            if i >= 3:
                raise RuntimeError("simulated ArcGIS mid-pagination failure")
            yield _feat(i)

    with pytest.raises(RuntimeError):
        db.load_features_staged(conn, "parcels", FIELDS, exploding, False,
                                lambda f: (None, None), expected_count=10)

    # live table unchanged — the good 2-row spine is still current
    assert _count(conn, "parcels") == 2
    # the partial pull is quarantined in staging, never promoted
    assert _table_exists(conn, "parcels__staging")
    assert _count(conn, "parcels__staging") < 10


# ---- resume continues into staging then promotes --------------------------

def test_resume_after_partial_completes(tmp_path):
    conn = _conn(tmp_path)

    def exploding(start):
        for i in range(start, 10):
            if i >= 4:
                raise RuntimeError("boom")
            yield _feat(i)

    # batch_size=2 so partial progress is committed before the failure
    with pytest.raises(RuntimeError):
        db.load_features_staged(conn, "parcels", FIELDS, exploding, False,
                                lambda f: (None, None), expected_count=10, batch_size=2)
    staged_before = _count(conn, "parcels__staging")
    assert 0 < staged_before < 10
    assert not _table_exists(conn, "parcels")  # never promoted

    # resume: continue from where staging left off, to completion
    total = db.load_features_staged(conn, "parcels", FIELDS,
                                    lambda s: (_feat(i) for i in range(s, 10)),
                                    False, lambda f: (None, None),
                                    expected_count=10, resume=True, batch_size=2)
    assert total == 10
    assert _count(conn, "parcels") == 10
    # no duplicates: resume started at the staged count, keys are unique
    assert conn.execute('SELECT COUNT(DISTINCT hcad_num) FROM parcels').fetchone()[0] == 10


# ---- reconciliation shortfall blocks promotion ----------------------------

def test_reconcile_shortfall_blocks_promotion(tmp_path):
    conn = _conn(tmp_path)
    # advertise 100 but only yield 5 -> shortfall, must not promote
    with pytest.raises(db.FeatureReconcileError):
        db.load_features_staged(conn, "parcels", FIELDS,
                                lambda s: (_feat(i) for i in range(s, 5)),
                                False, lambda f: (None, None),
                                expected_count=100, reconcile_tolerance=0.02)
    assert not _table_exists(conn, "parcels")       # never became current
    assert _table_exists(conn, "parcels__staging")  # partial retained in staging


def test_reconcile_within_tolerance_promotes(tmp_path):
    conn = _conn(tmp_path)
    # advertise 100, yield 99, 2% tolerance -> allowed
    total = db.load_features_staged(conn, "parcels", FIELDS,
                                    lambda s: (_feat(i) for i in range(s, 99)),
                                    False, lambda f: (None, None),
                                    expected_count=100, reconcile_tolerance=0.02)
    assert total == 99
    assert _count(conn, "parcels") == 99


def test_promote_migrates_scrape_meta(tmp_path):
    conn = _conn(tmp_path)
    db.load_features_staged(conn, "parcels", FIELDS,
                            lambda s: (_feat(i) for i in range(s, 3)),
                            False, lambda f: (None, None), expected_count=3)
    # provenance now keyed on the live table, not staging
    assert db.rows_fetched(conn, "parcels") >= 0
    meta = conn.execute("SELECT table_name FROM _scrape_meta").fetchall()
    names = {r[0] for r in meta}
    assert "parcels__staging" not in names
