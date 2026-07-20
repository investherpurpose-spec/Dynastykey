"""Fixture-based tests for the hardened HCAD bulk importer.

Builds real .zip archives containing a tab-delimited, cp1252-encoded
`real_acct.txt` (matching the documented HCAD bulk format) and exercises the
load path end to end: schema assertions, quarantine, duplicate handling,
reconciliation, checkpoint/resume, and last-good-output preservation.
"""

import sqlite3
import zipfile

import pytest

from scraper import hcad_bulk as hb


HEADER = ["acct", "mailto", "mail_addr_1"]


def _write_zip(path, header, data_rows, member="real_acct.txt", encoding="cp1252"):
    """Write a zip containing a tab-delimited text member.

    data_rows are raw lists of cell strings (so we can craft ragged/short rows).
    """
    lines = ["\t".join(header)]
    for r in data_rows:
        lines.append("\t".join(r))
    text = "\r\n".join(lines) + "\r\n"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member, text.encode(encoding, errors="replace"))
    return path


def _conn():
    return sqlite3.connect(":memory:")


def _rows(conn, table):
    return conn.execute(f'SELECT * FROM "{table}"').fetchall()


# ---- happy path -----------------------------------------------------------

def test_basic_load(tmp_path):
    z = _write_zip(tmp_path / "b.zip", HEADER, [
        ["0751890030033", "SMITH JOHN", "123 MAIN ST"],
        ["0751890030034", "DOE JANE", "456 OAK AVE"],
        ["0751890030035", "ACME LLC", "789 PINE RD"],
    ])
    conn = _conn()
    m = hb.load_owners(conn, z)
    assert m.status == "PASS"
    assert m.rows_read == 3
    assert m.rows_inserted == 3
    assert m.duplicates == 0
    assert m.quarantined == 0
    assert m.reconciled is True
    assert len(_rows(conn, "owners")) == 3
    # provenance recorded
    meta = conn.execute("SELECT rows_fetched FROM _scrape_meta WHERE table_name='owners'").fetchone()
    assert meta[0] == 3


def test_cp1252_encoding_preserved(tmp_path):
    # 0xD1 = Ñ in cp1252
    z = _write_zip(tmp_path / "e.zip", HEADER, [
        ["1", "PEÑA JOSE", "1 CALLE"],
    ])
    conn = _conn()
    hb.load_owners(conn, z)
    val = conn.execute("SELECT mailto FROM owners WHERE acct='1'").fetchone()[0]
    assert "PEÑA" in val


# ---- schema / input assertions -------------------------------------------

def test_missing_required_column_fails_loud(tmp_path):
    z = _write_zip(tmp_path / "s.zip", ["mailto", "mail_addr_1"], [
        ["SMITH JOHN", "123 MAIN"],
    ])
    conn = _conn()
    with pytest.raises(hb.HCADSchemaError):
        hb.load_owners(conn, z)


def test_missing_zip_fails(tmp_path):
    conn = _conn()
    with pytest.raises(hb.HCADInputError):
        hb.load_owners(conn, tmp_path / "nope.zip")


def test_not_a_zip_fails(tmp_path):
    p = tmp_path / "plain.zip"
    p.write_text("not a zip")
    conn = _conn()
    with pytest.raises(hb.HCADInputError):
        hb.load_owners(conn, p)


def test_no_real_acct_member_fails(tmp_path):
    p = tmp_path / "wrong.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("something_else.txt", "a\tb\n1\t2\n")
    conn = _conn()
    with pytest.raises(hb.HCADInputError):
        hb.load_owners(conn, p)


def test_expected_column_absence_is_warning_not_failure(tmp_path):
    # acct present (required) but mail_addr_1 absent (expected only)
    z = _write_zip(tmp_path / "w.zip", ["acct", "mailto"], [
        ["1", "SMITH JOHN"],
    ])
    conn = _conn()
    m = hb.load_owners(conn, z)
    assert m.status == "PASS"
    assert "mail_addr_1" in m.expected_missing


# ---- duplicate handling ---------------------------------------------------

def test_duplicate_acct_kept_first(tmp_path):
    z = _write_zip(tmp_path / "d.zip", HEADER, [
        ["1", "FIRST OWNER", "A"],
        ["1", "SECOND OWNER", "B"],  # duplicate acct
        ["2", "OTHER", "C"],
    ])
    conn = _conn()
    m = hb.load_owners(conn, z)
    assert m.rows_read == 3
    assert m.rows_inserted == 2
    assert m.duplicates == 1
    assert m.reconciled is True
    kept = conn.execute("SELECT mailto FROM owners WHERE acct='1'").fetchone()[0]
    assert kept == "FIRST OWNER"


# ---- malformed-row quarantine --------------------------------------------

def test_missing_acct_row_quarantined(tmp_path):
    z = _write_zip(tmp_path / "q.zip", HEADER, [
        ["1", "GOOD", "A"],
        ["", "NO ACCT", "B"],  # empty acct -> quarantine
        ["3", "ALSO GOOD", "C"],
    ])
    conn = _conn()
    m = hb.load_owners(conn, z)
    assert m.rows_inserted == 2
    assert m.quarantined == 1
    assert m.reconciled is True
    q = _rows(conn, "owners__quarantine")
    assert len(q) == 1
    assert q[0][1] == "missing_acct"


def test_extra_columns_row_quarantined(tmp_path):
    z = _write_zip(tmp_path / "x.zip", HEADER, [
        ["1", "GOOD", "A"],
        ["2", "BAD", "B", "EXTRA", "MORE"],  # too many columns
    ])
    conn = _conn()
    m = hb.load_owners(conn, z)
    assert m.rows_inserted == 1
    assert m.quarantined == 1
    q = _rows(conn, "owners__quarantine")
    assert q[0][1] == "extra_columns"


def test_short_row_with_acct_is_kept(tmp_path):
    # fewer columns than header, but acct present -> not malformed
    z = _write_zip(tmp_path / "short.zip", HEADER, [
        ["1", "ONLY NAME"],  # missing mail_addr_1 cell
    ])
    conn = _conn()
    m = hb.load_owners(conn, z)
    assert m.rows_inserted == 1
    assert m.quarantined == 0


# ---- reconciliation -------------------------------------------------------

def test_reconciliation_holds(tmp_path):
    z = _write_zip(tmp_path / "r.zip", HEADER, [
        ["1", "A", "x"], ["1", "B", "y"], ["", "C", "z"], ["2", "D", "w"],
    ])
    conn = _conn()
    m = hb.load_owners(conn, z)
    assert m.rows_read == m.rows_inserted + m.duplicates + m.quarantined
    assert (m.rows_inserted, m.duplicates, m.quarantined) == (2, 1, 1)


# ---- limit ----------------------------------------------------------------

def test_limit_caps_rows(tmp_path):
    z = _write_zip(tmp_path / "l.zip", HEADER,
                   [[str(i), f"OWNER {i}", "ADDR"] for i in range(100)])
    conn = _conn()
    m = hb.load_owners(conn, z, limit=10)
    assert m.rows_read == 10
    assert m.rows_inserted == 10


# ---- checkpoint / resume --------------------------------------------------

def _exploding_reader(monkeypatch, after_n):
    """Patch _open_reader so the stream raises after yielding `after_n` rows."""
    real_open = hb._open_reader

    def factory(text):
        r = real_open(text)
        fieldnames = r.fieldnames
        rows = list(r)

        class _R:
            def __init__(self):
                self.fieldnames = fieldnames

            def __iter__(self):
                for i, row in enumerate(rows):
                    if i >= after_n:
                        raise RuntimeError("simulated mid-load failure")
                    yield row

        return _R()

    monkeypatch.setattr(hb, "_open_reader", factory)


def test_resume_after_interrupted_load(tmp_path, monkeypatch):
    z = _write_zip(tmp_path / "res.zip", HEADER,
                   [[str(i), f"OWNER {i}", "ADDR"] for i in range(50)])
    conn = _conn()

    # interrupt the first load after 3 rows
    _exploding_reader(monkeypatch, after_n=3)
    with pytest.raises(RuntimeError):
        hb.load_owners(conn, z)
    monkeypatch.undo()

    # the interrupted run left a checkpoint and never published `owners`
    assert hb._read_checkpoint(conn, "owners") is not None
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE name='owners'").fetchone() is None

    # resume to completion
    m = hb.load_owners(conn, z, resume=True)
    assert m.status == "PASS"
    assert m.reconciled is True
    assert conn.execute("SELECT COUNT(*) FROM owners").fetchone()[0] == 50
    # checkpoint cleared after successful swap
    assert hb._read_checkpoint(conn, "owners") is None


def test_resume_wrong_source_rejected(tmp_path):
    z1 = _write_zip(tmp_path / "a.zip", HEADER, [["1", "A", "x"]])
    z2 = _write_zip(tmp_path / "b.zip", HEADER, [["2", "B", "y"]])
    conn = _conn()
    # seed a checkpoint by simulating an interrupted load of z1
    hb._ensure_meta_tables(conn)
    hb._write_checkpoint(conn, "owners", str(z1), "real_acct.txt", 999)
    conn.commit()
    with pytest.raises(hb.HCADInputError):
        hb.load_owners(conn, z2, resume=True)


# ---- last-good-output preservation ---------------------------------------

def test_failed_load_preserves_previous_table(tmp_path, monkeypatch):
    # 1) load a good table
    good = _write_zip(tmp_path / "good.zip", HEADER, [
        ["1", "GOOD OWNER", "A"], ["2", "SECOND", "B"],
    ])
    conn = _conn()
    hb.load_owners(conn, good)
    assert conn.execute("SELECT COUNT(*) FROM owners").fetchone()[0] == 2

    # 2) a second load that blows up mid-stream must NOT destroy the good table
    boom = _write_zip(tmp_path / "boom.zip", HEADER,
                      [[str(i), f"O{i}", "A"] for i in range(10)])
    _exploding_reader(monkeypatch, after_n=3)
    with pytest.raises(RuntimeError):
        hb.load_owners(conn, boom)
    monkeypatch.undo()

    # previous good spine survived untouched
    assert conn.execute("SELECT COUNT(*) FROM owners").fetchone()[0] == 2
    assert conn.execute("SELECT mailto FROM owners WHERE acct='1'").fetchone()[0] == "GOOD OWNER"
    # the failed run recorded a FAIL manifest
    import json
    man = json.loads(conn.execute(
        "SELECT manifest FROM _load_manifest WHERE table_name='owners'").fetchone()[0])
    assert man["status"] == "FAIL"


# ---- manifest persistence -------------------------------------------------

def test_manifest_persisted(tmp_path):
    z = _write_zip(tmp_path / "m.zip", HEADER, [["1", "A", "x"]])
    conn = _conn()
    m = hb.load_owners(conn, z)
    row = conn.execute("SELECT manifest FROM _load_manifest WHERE table_name='owners'").fetchone()
    assert row is not None
    import json
    stored = json.loads(row[0])
    assert stored["status"] == "PASS"
    assert stored["rows_inserted"] == 1


def test_iter_owner_rows_backward_compat(tmp_path):
    z = _write_zip(tmp_path / "i.zip", HEADER, [["1", "A", "x"], ["2", "B", "y"]])
    rows = list(hb.iter_owner_rows(z))
    assert len(rows) == 2
    assert rows[0]["acct"] == "1"
    assert rows[0]["mailto"] == "A"
