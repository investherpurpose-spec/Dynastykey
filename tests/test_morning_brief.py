"""Tests for scraper.morning_brief — the Aurora Morning Brief renderer.

These prove two things at once:
  * when a real source has data, the section renders it faithfully;
  * when a source does not exist, the section says so explicitly and invents
    nothing (no scores, no urgency, no market movement).
"""

import json
import sqlite3

import pytest

from scraper import morning_brief as mb


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _spine_db(parcels, owners, parcel_cols=("hcad_num", "site_addr_1", "owner")):
    """parcels rows are tuples matching parcel_cols; owners are (acct, mailto, mail_addr_1)."""
    conn = sqlite3.connect(":memory:")
    conn.execute(f'CREATE TABLE parcels ({", ".join(f"{c} TEXT" for c in parcel_cols)})')
    conn.executemany(
        f'INSERT INTO parcels VALUES ({",".join("?" for _ in parcel_cols)})', parcels)
    conn.execute('CREATE TABLE owners (acct TEXT, mailto TEXT, mail_addr_1 TEXT)')
    conn.executemany('INSERT INTO owners VALUES (?,?,?)', owners)
    conn.commit()
    return conn


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def _now():
    from datetime import datetime, timezone
    return datetime(2026, 8, 7, 6, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# 1. CALL TODAY
# --------------------------------------------------------------------------

def test_call_today_absent_ranking_is_honest_empty():
    sec = mb.section_call_today(None)
    assert sec.available is False
    assert not sec.items
    assert "not present" in sec.reason


def test_call_today_renders_injected_ranking_without_inventing():
    ranking = [
        {"address": "123 Elm St", "owner": "JANE DOE", "score": 82,
         "distress_signals": ["tax_delinquent", "vacant"],
         "strategy": "cash offer", "why": "foreclosure + high equity"},
        {"address": "9 Oak Ave", "owner": "ACME LLC", "score": 61,
         "signals": "probate", "why": "estate lead"},
    ]
    sec = mb.section_call_today(None, ranking=ranking)
    assert sec.available is True
    assert len(sec.items) == 2
    top = sec.items[0]
    assert top.title == "123 Elm St"
    assert top.fields["score"] == 82
    assert top.fields["distress_signals"] == ["tax_delinquent", "vacant"]
    assert top.fields["strategy"] == "cash offer"
    assert top.why == "foreclosure + high equity"
    # string signals get normalized to a list
    assert sec.items[1].fields["distress_signals"] == ["probate"]


def test_call_today_reads_ranked_leads_table_if_present():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ranked_leads (rank INT, address TEXT, score INT, why TEXT)")
    conn.executemany("INSERT INTO ranked_leads VALUES (?,?,?,?)",
                     [(1, "1 A St", 90, "top"), (2, "2 B St", 70, "next")])
    conn.commit()
    sec = mb.section_call_today(conn)
    assert sec.available is True
    assert [i.title for i in sec.items] == ["1 A St", "2 B St"]


# --------------------------------------------------------------------------
# 2. REVIEW TODAY  — the real identity-resolution section
# --------------------------------------------------------------------------

def test_review_surfaces_single_candidate_confirmation():
    # parcel "9" is unmatched by key-join (no acct 9) but its owner name matches
    # exactly one owner record -> a confirmation worth a human.
    conn = _spine_db(
        parcels=[("1", "A ST", "SMITH JOHN"),      # matched by key
                 ("9", "9 ELM ST", "MARIA GARCIA")],  # unmatched, name resolves
        owners=[("1", "SMITH JOHN", "1 A"),
                ("500", "GARCIA MARIA", "9 ELM")],
    )
    sec = mb.section_review_today(conn)
    assert sec.available is True
    assert len(sec.items) == 1
    it = sec.items[0]
    assert it.fields["parcel"] == "9"
    assert it.fields["candidate_acct"] == "500"
    assert it.priority == mb.REVIEW
    assert "confirm" in it.why.lower()


def test_review_defers_ambiguous_multi_candidate():
    # unmatched parcel whose name matches TWO owners -> ambiguous, not shown
    conn = _spine_db(
        parcels=[("9", "9 ELM ST", "JOHN SMITH")],
        owners=[("100", "JOHN SMITH", "a"), ("101", "JOHN SMITH", "b")],
    )
    sec = mb.section_review_today(conn)
    assert sec.available is True
    assert sec.items == []
    assert "ambiguous" in sec.reason


def test_review_caps_output():
    parcels = [(str(1000 + i), f"{i} ST", f"OWNER{i:03d} X") for i in range(20)]
    owners = [(str(9000 + i), f"OWNER{i:03d} X", "a") for i in range(20)]
    conn = _spine_db(parcels=parcels, owners=owners)
    sec = mb.section_review_today(conn, cap=6)
    assert len(sec.items) == 6              # never floods
    assert sec.__dict__["_single_total"] == 20  # but knows the true count
    # deterministic: capped set is the lexically-first parcels
    assert [i.fields["parcel"] for i in sec.items] == [str(1000 + i) for i in range(6)]


def test_review_unavailable_without_tables():
    conn = sqlite3.connect(":memory:")
    sec = mb.section_review_today(conn)
    assert sec.available is False
    assert "not loaded" in sec.reason


def test_review_unavailable_without_owner_name_column_on_parcels():
    conn = _spine_db(parcels=[("1", "A ST")], owners=[("1", "X", "a")],
                     parcel_cols=("hcad_num", "site_addr_1"))
    sec = mb.section_review_today(conn)
    assert sec.available is False
    assert "owner-name" in sec.reason


def test_review_restores_row_factory():
    conn = _spine_db(parcels=[("9", "9 ELM", "MARIA GARCIA")],
                     owners=[("500", "GARCIA MARIA", "a")])
    mb.section_review_today(conn)
    assert conn.row_factory is None  # matching side-effect cleaned up


# --------------------------------------------------------------------------
# 3. URGENT CHANGES  — existing events only
# --------------------------------------------------------------------------

def test_urgent_none_without_work_dir(tmp_path):
    sec = mb.section_urgent(str(tmp_path / "nope"), _now())
    assert sec.available is False
    assert "no events" in sec.reason.lower()


def test_urgent_flags_failed_run(tmp_path):
    _write_json(tmp_path / "latest_attempt.json",
                {"run_id": "r1", "status": "FAIL", "published": False,
                 "reasons": ["identity_join: material regression in join_rate"]})
    sec = mb.section_urgent(str(tmp_path), _now())
    assert sec.available is True
    assert any(i.priority == mb.URGENT for i in sec.items)
    assert any("regression" in i.why for i in sec.items)


def test_urgent_new_since_last_brief_diff(tmp_path):
    _write_json(tmp_path / "latest_attempt.json",
                {"run_id": "r2", "status": "FAIL", "published": False, "reasons": ["boom"]})
    # first brief: r2 is new
    sec1 = mb.section_urgent(str(tmp_path), _now(), prev_state={"failed_run_ids": []})
    assert sec1.items[0].new_since_last_brief is True
    # after we've seen r2, it is no longer new
    sec2 = mb.section_urgent(str(tmp_path), _now(), prev_state={"failed_run_ids": ["r2"]})
    assert sec2.items[0].new_since_last_brief is False


def test_urgent_stale_publication(tmp_path):
    _write_json(tmp_path / "latest_attempt.json",
                {"run_id": "r1", "status": "PASS", "published": True})
    _write_json(tmp_path / "latest_success.json",
                {"run_id": "r0", "status": "PASS", "published": True,
                 "ended_at": "2026-08-01T00:00:00Z"})  # ~6 days before _now()
    sec = mb.section_urgent(str(tmp_path), _now())
    assert any("stale" in i.title.lower() for i in sec.items)


def test_urgent_quiet_when_healthy_and_current(tmp_path):
    _write_json(tmp_path / "latest_attempt.json",
                {"run_id": "r1", "status": "PASS", "published": True})
    _write_json(tmp_path / "latest_success.json",
                {"run_id": "r1", "status": "PASS", "published": True,
                 "ended_at": "2026-08-07T00:00:00Z"})  # fresh
    sec = mb.section_urgent(str(tmp_path), _now())
    assert sec.items == []
    assert "healthy" in sec.reason.lower()


def test_urgent_does_not_invent_foreclosure_dates(tmp_path):
    _write_json(tmp_path / "latest_attempt.json",
                {"run_id": "r1", "status": "PASS", "published": True})
    sec = mb.section_urgent(str(tmp_path), _now())
    blob = json.dumps(sec.to_dict()).lower()
    assert "auction" not in blob or "no feed" in blob


# --------------------------------------------------------------------------
# 4. NEIGHBORHOOD WATCH
# --------------------------------------------------------------------------

def test_neighborhood_explicit_unavailable_message():
    sec = mb.section_neighborhood(None)
    assert sec.available is False
    assert sec.reason == "Market trend intelligence not yet available."
    assert sec.items == []


# --------------------------------------------------------------------------
# 5. TODAY'S FOCUS + assembly
# --------------------------------------------------------------------------

def test_focus_prioritizes_urgent(tmp_path):
    _write_json(tmp_path / "latest_attempt.json",
                {"run_id": "r1", "status": "FAIL", "published": False, "reasons": ["x"]})
    brief = mb.build_brief(conn=None, work_dir=str(tmp_path), now=_now())
    assert brief.focus.startswith("Resolve the urgent")


def test_focus_review_when_no_urgent():
    conn = _spine_db(parcels=[("9", "9 ELM", "MARIA GARCIA")],
                     owners=[("500", "GARCIA MARIA", "a")])
    brief = mb.build_brief(conn=conn, work_dir=None, now=_now())
    assert "identity match" in brief.focus


def test_focus_calm_when_nothing_to_do():
    brief = mb.build_brief(conn=None, work_dir=None, now=_now())
    assert "No action required" in brief.focus
    assert brief.actionable_count == 0


def test_actionable_count_protects_attention(tmp_path):
    # a big pile of unmatched parcels, but the brief only counts the shown few
    parcels = [(str(1000 + i), f"{i} ST", f"OWNER{i:03d} X") for i in range(50)]
    owners = [(str(9000 + i), f"OWNER{i:03d} X", "a") for i in range(50)]
    conn = _spine_db(parcels=parcels, owners=owners)
    brief = mb.build_brief(conn=conn, work_dir=str(tmp_path), now=_now(), review_cap=6)
    assert brief.actionable_count == 6           # shows the 6, not the 50
    assert brief.records_scanned == 50           # but is transparent about scope


def test_build_brief_has_all_five_sections():
    brief = mb.build_brief(conn=None, work_dir=None, now=_now())
    keys = [s.key for s in brief.sections]
    assert keys == ["call_today", "review_today", "urgent_changes", "neighborhood_watch"]
    assert brief.focus  # section 5 is the focus line


# --------------------------------------------------------------------------
# state persistence + rendering
# --------------------------------------------------------------------------

def test_state_roundtrip(tmp_path):
    brief = mb.build_brief(conn=None, work_dir=str(tmp_path), now=_now())
    mb.save_state(str(tmp_path), brief)
    loaded = mb.load_state(str(tmp_path))
    assert loaded["brief_at"] == brief.state_fingerprint["brief_at"]


def test_load_state_missing_is_empty(tmp_path):
    assert mb.load_state(str(tmp_path / "nope")) == {}


def test_render_text_is_one_screen_and_plain():
    brief = mb.build_brief(conn=None, work_dir=None, now=_now())
    text = mb.render_text(brief, color=False)
    assert "AURORA" in text
    assert "CALL TODAY" in text and "TODAY'S FOCUS" in text
    assert "Market trend intelligence not yet available" in text
    assert "\033[" not in text  # no color codes when color=False
    assert len(text.splitlines()) < 40  # fits a screen


def test_render_text_color_only_when_requested():
    brief = mb.build_brief(conn=None, work_dir=None, now=_now())
    assert "\033[" in mb.render_text(brief, color=True)


def test_to_dict_is_json_serializable():
    brief = mb.build_brief(conn=None, work_dir=None, now=_now())
    json.dumps(brief.to_dict())  # must not raise


def test_build_brief_is_deterministic():
    conn1 = _spine_db(parcels=[("9", "9 ELM", "MARIA GARCIA")],
                      owners=[("500", "GARCIA MARIA", "a")])
    conn2 = _spine_db(parcels=[("9", "9 ELM", "MARIA GARCIA")],
                      owners=[("500", "GARCIA MARIA", "a")])
    b1 = mb.build_brief(conn=conn1, work_dir=None, now=_now())
    b2 = mb.build_brief(conn=conn2, work_dir=None, now=_now())
    assert b1.to_dict() == b2.to_dict()
