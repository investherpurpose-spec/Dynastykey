"""Tests for scraper.spine — identity-spine metrics and regression checks."""

import sqlite3

from scraper import spine


def _db(parcels, owners, with_points=False):
    conn = sqlite3.connect(":memory:")
    if with_points:
        conn.execute('CREATE TABLE parcels (hcad_num TEXT, site_addr_1 TEXT, '
                     '_point_lon REAL, _point_lat REAL)')
        conn.executemany('INSERT INTO parcels VALUES (?,?,?,?)', parcels)
    else:
        conn.execute('CREATE TABLE parcels (hcad_num TEXT, site_addr_1 TEXT)')
        conn.executemany('INSERT INTO parcels VALUES (?,?)', parcels)
    conn.execute('CREATE TABLE owners (acct TEXT, mailto TEXT, mail_addr_1 TEXT)')
    conn.executemany('INSERT INTO owners VALUES (?,?,?)', owners)
    conn.commit()
    return conn


# ---- metrics --------------------------------------------------------------

def test_basic_metrics_full_match():
    conn = _db(
        parcels=[("1", "A ST"), ("2", "B ST"), ("3", "C ST")],
        owners=[("1", "SMITH", "1 A"), ("2", "DOE", "2 B"), ("3", "ACME", "3 C")],
    )
    m = spine.compute_spine_metrics(conn)
    assert m.parcel_rows == 3 and m.owner_rows == 3
    assert m.unique_parcels == 3 and m.unique_accounts == 3
    assert m.matched_parcels == 3 and m.unmatched_parcels == 0
    assert m.join_rate == 1.0
    assert m.owner_name_completeness == 1.0
    assert m.parcel_addr_completeness == 1.0


def test_unmatched_and_join_rate():
    conn = _db(
        parcels=[("1", "A"), ("2", "B"), ("3", "C"), ("4", "D")],
        owners=[("1", "X", "a"), ("2", "Y", "b")],  # only 2 of 4 match
    )
    m = spine.compute_spine_metrics(conn)
    assert m.matched_parcels == 2
    assert m.unmatched_parcels == 2
    assert m.join_rate == 0.5


def test_duplicate_keys():
    conn = _db(
        parcels=[("1", "A"), ("1", "A2"), ("2", "B")],  # dup parcel key
        owners=[("1", "X", "a"), ("2", "Y", "b")],
    )
    m = spine.compute_spine_metrics(conn)
    assert m.unique_parcels == 2
    assert m.duplicate_parcel_keys == 1


def test_completeness_partial():
    conn = _db(
        parcels=[("1", "A"), ("2", "")],       # one blank address
        owners=[("1", "X", "a"), ("2", "", "b")],  # one blank owner name
    )
    m = spine.compute_spine_metrics(conn)
    assert m.owner_name_completeness == 0.5
    assert m.parcel_addr_completeness == 0.5


def test_gis_validity_with_points():
    conn = _db(
        parcels=[("1", "A", -95.37, 29.76),   # in Harris bbox
                 ("2", "B", 0, 0),             # null island
                 ("3", "C", -80.0, 40.0),      # far away
                 ("4", "D", -95.0, 29.9)],     # in bbox
        owners=[("1", "X", "a")],
        with_points=True,
    )
    m = spine.compute_spine_metrics(conn)
    assert m.gis_validity == 0.5  # 2 of 4 valid


def test_gis_validity_none_without_points():
    conn = _db(parcels=[("1", "A")], owners=[("1", "X", "a")])
    m = spine.compute_spine_metrics(conn)
    assert m.gis_validity is None


def test_missing_tables_flagged():
    conn = sqlite3.connect(":memory:")
    m = spine.compute_spine_metrics(conn)
    assert any("missing" in w for w in m.warnings)


# ---- check_spine grading --------------------------------------------------

def test_check_pass_no_prior_is_baseline():
    conn = _db(parcels=[("1", "A"), ("2", "B")],
               owners=[("1", "X", "a"), ("2", "Y", "b")])
    r = spine.check_spine(conn)
    assert r.status == spine.PASS
    assert any("baseline" in reason for reason in r.reasons)


def test_check_fail_empty_spine():
    conn = _db(parcels=[], owners=[])
    r = spine.check_spine(conn)
    assert r.status == spine.FAIL


def test_check_partial_low_join_rate():
    conn = _db(parcels=[("1", "A"), ("2", "B"), ("3", "C"), ("4", "D")],
               owners=[("1", "X", "a")])  # 25% join
    r = spine.check_spine(conn)
    assert r.status == spine.PARTIAL
    assert any("join_rate" in reason for reason in r.reasons)


def test_check_fail_material_regression():
    conn = _db(parcels=[("1", "A"), ("2", "B")],
               owners=[("1", "X", "a"), ("2", "Y", "b")])
    # prior had 100 unique accounts; now only 2 -> massive drop
    prior = {"unique_parcels": 2, "unique_accounts": 100, "join_rate": 1.0,
             "owner_name_completeness": 1.0, "parcel_addr_completeness": 1.0}
    r = spine.check_spine(conn, prior_metrics=prior)
    assert r.status == spine.FAIL
    assert any("material regression in unique_accounts" in reason for reason in r.reasons)


def test_check_pass_within_tolerance():
    conn = _db(parcels=[("1", "A"), ("2", "B"), ("3", "C")],
               owners=[("1", "X", "a"), ("2", "Y", "b"), ("3", "Z", "c")])
    # prior slightly higher but within 10% drop tolerance
    prior = {"unique_parcels": 3, "unique_accounts": 3, "join_rate": 1.0,
             "owner_name_completeness": 1.0, "parcel_addr_completeness": 1.0}
    r = spine.check_spine(conn, prior_metrics=prior)
    assert r.status == spine.PASS


def test_check_accepts_wrapped_prior():
    conn = _db(parcels=[("1", "A")], owners=[("1", "X", "a")])
    prior = {"metrics": {"unique_parcels": 1, "unique_accounts": 1, "join_rate": 1.0,
                         "owner_name_completeness": 1.0, "parcel_addr_completeness": 1.0}}
    r = spine.check_spine(conn, prior_metrics=prior)
    assert r.status == spine.PASS
