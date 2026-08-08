"""Regression tests for the operator validation-import boundary.

Covers the Excel scientific-notation corruption of 13-digit HCAD account numbers
and the deterministic, refuse-to-guess recovery rule. These tests exercise ONLY
the import boundary (`scraper.validation_import`); no resolver, legal matcher,
scorer, or Aurora code is involved.
"""

import re

import pytest

from scraper import validation_import as vi


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def excel_sci(account: str, sigfigs: int = 6) -> str:
    """Reproduce Excel's scientific-notation mangling of a 13-digit account.

    ``"1002580000022"`` -> ``"1.00258E+12"`` (Excel's default ~6 significant
    figures). Leading zeros are dropped because Excel treats the cell as a number.
    """
    return f"{int(account):.{sigfigs - 1}E}"


# The two worked examples from the defect report.
NORMAL_ACCT = "1002580000022"       # -> 1.00258E+12
LEADZERO_ACCT = "0690800040036"     # -> 6.90800E+11 (int is 12 digits)


# --------------------------------------------------------------------------
# 1. leading-zero account: 0690800040036
# --------------------------------------------------------------------------

def test_leading_zero_account_preserved_as_canonical():
    # already-canonical plain string with a significant leading zero -> unchanged
    rec = vi.recover_account(LEADZERO_ACCT, candidates=[])
    assert rec.status == vi.CANONICAL
    assert rec.account == LEADZERO_ACCT
    assert len(rec.account) == vi.ACCOUNT_WIDTH


def test_leading_zero_recovered_from_scientific_keeps_zero():
    # Excel dropped the leading zero AND scientific-mangled it; recovery must
    # restore the exact 13-char canonical string, leading zero intact.
    sci = excel_sci(LEADZERO_ACCT)          # "6.90800E+11"
    assert "E" in sci and not sci.startswith("0")
    rec = vi.recover_account(sci, candidates=[LEADZERO_ACCT, "1002580000022"])
    assert rec.status == vi.RECOVERED
    assert rec.account == LEADZERO_ACCT
    assert rec.account[0] == "0"


# --------------------------------------------------------------------------
# 2. normal 13-digit account: 1002580000022
# --------------------------------------------------------------------------

def test_normal_13_digit_account_is_canonical_unchanged():
    rec = vi.recover_account(NORMAL_ACCT, candidates=[])
    assert rec.status == vi.CANONICAL
    assert rec.account == NORMAL_ACCT


def test_plain_13_digit_string_is_not_scientific():
    assert vi.is_scientific(NORMAL_ACCT) is False
    assert vi.is_scientific(LEADZERO_ACCT) is False


# --------------------------------------------------------------------------
# 3. scientific notation recovery (the core fix)
# --------------------------------------------------------------------------

def test_scientific_recovery_single_consistent_candidate():
    sci = excel_sci(NORMAL_ACCT)            # "1.00258E+12"
    assert vi.is_scientific(sci)
    # candidate set (from the uncorrupted legal index) contains the true account
    # plus unrelated accounts that are NOT consistent with the sci value.
    candidates = ["1002580000022", "1009990000001", "0690800040036"]
    rec = vi.recover_account(sci, candidates)
    assert rec.status == vi.RECOVERED
    assert rec.account == NORMAL_ACCT
    assert rec.consistent == [NORMAL_ACCT]


def test_scientific_value_never_used_as_canonical_directly():
    sci = excel_sci(NORMAL_ACCT)
    # with no candidate set there is nothing to recover against -> must NOT
    # canonicalize the sci string itself; must refuse.
    rec = vi.recover_account(sci, candidates=[])
    assert rec.status == vi.AMBIGUOUS
    assert rec.account is None


# --------------------------------------------------------------------------
# 4. ambiguous scientific notation -> must refuse to guess
# --------------------------------------------------------------------------

def test_ambiguous_multiple_consistent_candidates_refuses():
    sci = excel_sci(NORMAL_ACCT)            # "1.00258E+12"
    # two real accounts share the first 6 significant figures -> both consistent
    twin_a = "1002580000022"
    twin_b = "1002580000099"
    assert excel_sci(twin_a) == excel_sci(twin_b) == sci
    rec = vi.recover_account(sci, [twin_a, twin_b])
    assert rec.status == vi.AMBIGUOUS
    assert rec.account is None
    assert set(rec.consistent) == {twin_a, twin_b}


def test_ambiguous_zero_consistent_candidates_refuses():
    sci = excel_sci(NORMAL_ACCT)
    rec = vi.recover_account(sci, ["9999990000001", "8888880000002"])
    assert rec.status == vi.AMBIGUOUS
    assert rec.account is None
    assert rec.consistent == []


def test_corrupted_candidate_cannot_serve_as_canonical_source():
    # a candidate that is ITSELF scientific-notation-corrupted is not a valid
    # canonical source and must be ignored -> no clean candidate -> ambiguous.
    sci = excel_sci(NORMAL_ACCT)
    rec = vi.recover_account(sci, [excel_sci(NORMAL_ACCT)])
    assert rec.status == vi.AMBIGUOUS
    assert rec.account is None


# --------------------------------------------------------------------------
# 5. already-canonical account -> unchanged
# --------------------------------------------------------------------------

def test_already_canonical_unchanged_even_with_candidates():
    rec = vi.recover_account(NORMAL_ACCT, candidates=["9999990000001"])
    assert rec.status == vi.CANONICAL
    assert rec.account == NORMAL_ACCT


def test_short_plain_digits_zero_padded_to_canonical_width():
    rec = vi.recover_account("690800040036", candidates=[])  # 12 digits, no sci
    assert rec.status == vi.CANONICAL
    assert rec.account == LEADZERO_ACCT  # zero-padded to 13, digits preserved


def test_unrecognized_value_skipped():
    rec = vi.recover_account("N/A", candidates=[])
    assert rec.status == vi.SKIPPED
    assert rec.account is None
    rec2 = vi.recover_account("", candidates=[])
    assert rec2.status == vi.SKIPPED


# --------------------------------------------------------------------------
# 6. 32-case scenario (mirrors the operator's batch) — reports the four counts
# --------------------------------------------------------------------------

def _make_32_case_batch():
    """Build a representative 32-row reviewed batch + a matching legal index.

    Composition (mirrors a batch of manually-correct rows corrupted by Excel):
      - 24 scientific-notation rows recoverable via a single legal-index candidate
      -  4 already-canonical rows (plain 13-digit strings)
      -  2 ambiguous rows (one multi-candidate, one zero-candidate)
      -  2 skipped rows (empty / non-numeric)
    Returns (rows, legal_index) where legal_index maps (sub,lot,block)->[accts].
    """
    rows = []
    legal_index = {}

    def key(i):
        return (f"SUBDIV_{i:02d}", str(i), str((i % 5) + 1))

    # 24 recoverable scientific-notation rows
    for i in range(24):
        acct = f"{1000000000000 + i * 7000003:013d}"[:13]
        acct = acct.zfill(13)[-13:]
        sub, lot, block = key(i)
        legal_index[(sub, lot, block)] = [acct]
        rows.append({
            "record_id": f"RP-2026-{300000 + i}",
            "subdivision": sub, "lot": lot, "block": block,
            "expected_acct": excel_sci(acct),
            "reviewer_decision": "CONFIRMED",
            "reviewer_notes": f"note-{i}",
        })

    # 4 already-canonical rows (no Excel corruption)
    for j in range(24, 28):
        acct = f"{1200000000000 + j:013d}"
        sub, lot, block = key(j)
        legal_index[(sub, lot, block)] = [acct]
        rows.append({
            "record_id": f"RP-2026-{300000 + j}",
            "subdivision": sub, "lot": lot, "block": block,
            "expected_acct": acct,
            "reviewer_decision": "CONFIRMED",
            "reviewer_notes": f"note-{j}",
        })

    # 1 ambiguous — two legal candidates share the sci signature
    sub, lot, block = key(28)
    legal_index[(sub, lot, block)] = ["1002580000022", "1002580000099"]
    rows.append({
        "record_id": "RP-2026-300028",
        "subdivision": sub, "lot": lot, "block": block,
        "expected_acct": excel_sci("1002580000022"),
        "reviewer_decision": "CONFIRMED", "reviewer_notes": "twins",
    })

    # 1 ambiguous — no legal candidate matches the sci value
    sub, lot, block = key(29)
    legal_index[(sub, lot, block)] = ["7777770000001"]
    rows.append({
        "record_id": "RP-2026-300029",
        "subdivision": sub, "lot": lot, "block": block,
        "expected_acct": excel_sci("1002580000022"),
        "reviewer_decision": "CONFIRMED", "reviewer_notes": "no-match",
    })

    # 2 skipped — unusable account cell
    for k, bad in enumerate(("", "PENDING")):
        sub, lot, block = key(30 + k)
        legal_index[(sub, lot, block)] = ["1002580000022"]
        rows.append({
            "record_id": f"RP-2026-{300030 + k}",
            "subdivision": sub, "lot": lot, "block": block,
            "expected_acct": bad,
            "reviewer_decision": "CONFIRMED", "reviewer_notes": f"skip-{k}",
        })

    assert len(rows) == 32
    return rows, legal_index


def test_32_case_batch_counts():
    rows, legal_index = _make_32_case_batch()

    def legal_lookup(sub, lot, block):
        return legal_index.get((sub, lot, block), [])

    report = vi.import_reviewed_rows(rows, legal_lookup=legal_lookup)

    assert report.recovered_count == 24        # recovered from scientific notation
    assert len(report.canonical) == 4          # already-canonical, preserved
    assert report.imported_count == 28         # recovered + already-canonical
    assert report.ambiguous_count == 2         # refused to guess (needs manual)
    assert report.skipped_count == 2           # unusable cells
    # every count is accounted for
    assert (report.imported_count + report.ambiguous_count
            + report.skipped_count) == 32

    # every recovered account is a real 13-digit canonical string, never a sci value
    for rr in report.recovered:
        assert re.fullmatch(r"[0-9]{13}", rr.recovery.account)
        assert "E" not in rr.recovery.account


def test_import_does_not_mutate_rows_or_reviewer_notes():
    rows, legal_index = _make_32_case_batch()
    before = [dict(r) for r in rows]
    vi.import_reviewed_rows(rows, legal_lookup=lambda s, l, b: legal_index.get((s, l, b), []))
    assert rows == before  # reviewer decisions/notes and every cell untouched


def test_import_reviewed_csv_is_read_only(tmp_path):
    import csv as _csv

    rows, legal_index = _make_32_case_batch()
    csv_path = tmp_path / "validation_sample.csv"
    fields = ["record_id", "subdivision", "lot", "block", "expected_acct",
              "reviewer_decision", "reviewer_notes"]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = _csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})

    original_bytes = csv_path.read_bytes()
    report = vi.import_reviewed_csv(
        csv_path, legal_lookup=lambda s, l, b: legal_index.get((s, l, b), []))

    # the operator's file is never written back
    assert csv_path.read_bytes() == original_bytes
    assert report.imported_count == 28
    assert report.ambiguous_count == 2
    assert report.skipped_count == 2
    # ambiguous rows are retained for manual confirmation, with their notes intact
    assert all(rr.row.get("reviewer_notes") for rr in report.needs_manual)
