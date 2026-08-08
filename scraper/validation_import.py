"""Operator boundary: import a manually-reviewed validation CSV into verified cases.

## What this module is (and is NOT)

This is the **operator/data-ingestion boundary** — the seam where a human's
manually-reviewed CSV (opened and saved in Excel on a Windows machine) is read
back into the pipeline. It exists to defend that seam against one specific,
verified data-corruption defect and nothing more.

It does **NOT** do identity resolution, legal-description matching, scoring,
Aurora selection, or touch any engine. The set of candidate accounts it checks
against is an **input** to this module — supplied by the caller (the resolver's
already-returned candidate set, or an injected legal-index lookup). This module
never generates candidates and never decides who owns what; it only canonicalizes
the operator-supplied account value at the boundary.

## The defect this fixes

HCAD account numbers are 13-digit strings (e.g. ``1002580000022``, and with a
leading zero ``0690800040036``). When the operator opens/saves ``validation_sample.csv``
in Excel, Excel silently reformats those long numbers into **scientific
notation** — ``1002580000022`` becomes ``1.00258E+12``. A scientific-notation
string is a lossy, rounded display value: it is **never** a canonical parcel ID.
Every regression test that compared such a value against the resolver's candidate
set failed, even when the correct canonical account was present in that set.

## The rule (deterministic, refuses to guess)

For each reviewed row, given the operator's account value and a set of *clean*
(uncorrupted) candidate canonical accounts:

1. A scientific-notation value is **never** treated as a canonical account.
2. If the value is scientific notation, recover it against the candidate set:
   a candidate is *consistent* with the value when the candidate, formatted to
   the same number of significant figures the value shows, reproduces the value
   exactly. The canonical account is recovered **only when exactly one** candidate
   is consistent. Leading zeros are preserved (candidates are canonical 13-char
   strings).
3. If zero or more-than-one candidates are consistent, recovery is **ambiguous**:
   the row is not imported and is reported as needing manual account confirmation.
   The module never guesses.
4. If the value is already a full canonical account string, it is preserved as-is
   (zero-padded to the 13-digit canonical width, which is a no-op for an already
   13-digit value).

The candidate set must come from a source that did **not** pass through Excel
(the legal index, or the resolver's live candidate output) — a candidate that is
itself scientific-notation-corrupted cannot serve as a canonical source and is
ignored.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

# HCAD account numbers are 13-digit strings (leading zeros significant).
ACCOUNT_WIDTH = 13

# A scientific-notation number, e.g. "1.00258E+12", "6.90800E+11", "1E+12".
_SCI_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)[eE]([+-]?[0-9]+)\s*$")
# A plain, un-corrupted digit string (what a canonical account looks like as text).
_DIGITS_RE = re.compile(r"^\s*([0-9]{1,%d})\s*$" % ACCOUNT_WIDTH)

# Row-outcome statuses.
CANONICAL = "canonical"      # value already a full canonical account -> imported
RECOVERED = "recovered"      # sci-notation, exactly one consistent candidate -> imported
AMBIGUOUS = "ambiguous"      # sci-notation, 0 or >=2 consistent candidates -> needs manual
SKIPPED = "skipped"          # unusable value (empty / unrecognized) -> not imported

IMPORTED_STATUSES = (CANONICAL, RECOVERED)


# --------------------------------------------------------------------------
# Scientific-notation detection + canonicalization primitives
# --------------------------------------------------------------------------

def is_scientific(value) -> bool:
    """True iff *value* is a scientific-notation numeric string (has an exponent).

    A plain digit string like ``"1002580000022"`` is NOT scientific notation.
    """
    return isinstance(value, str) and _SCI_RE.match(value) is not None


def _sci_signature(value: str) -> Optional[tuple[str, int]]:
    """Normalize a scientific string to ``(significant_digits, exponent)``.

    ``"1.00258E+12"`` -> ``("100258", 12)``. The count of significant digits is
    preserved (trailing zeros in the mantissa are significant), so the same
    formatting applied to a candidate can be compared exactly.
    """
    m = _SCI_RE.match(value)
    if not m:
        return None
    digits = m.group(1).replace(".", "")
    return digits, int(m.group(2))


def _sci_sigfigs(value: str) -> Optional[int]:
    sig = _sci_signature(value)
    return None if sig is None else len(sig[0])


def canonical_account(value) -> Optional[str]:
    """Return the canonical 13-digit form of a *plain* account value, else None.

    Zero-pads to :data:`ACCOUNT_WIDTH` (a no-op for an already-13-digit value),
    preserving significant leading zeros. Returns None for empty, scientific,
    or otherwise non-plain-digit values — those must never be treated as
    canonical here.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s or is_scientific(s):
        return None
    m = _DIGITS_RE.match(s)
    if not m:
        return None
    return m.group(1).zfill(ACCOUNT_WIDTH)


def _clean_candidates(candidates: Iterable) -> list[str]:
    """Canonicalize a candidate set to unique 13-digit strings, dropping any
    candidate that is empty or itself scientific-notation-corrupted (a corrupted
    candidate cannot be a canonical source)."""
    out: list[str] = []
    seen: set[str] = set()
    for c in candidates or ():
        canon = canonical_account(c)
        if canon is not None and canon not in seen:
            seen.add(canon)
            out.append(canon)
    return out


def consistent_candidates(sci_value: str, candidates: Iterable) -> list[str]:
    """Return the canonical candidates consistent with a scientific-notation value.

    A candidate ``c`` is consistent when ``int(c)``, formatted to the same number
    of significant figures the value shows, reproduces the value exactly.
    """
    sig_target = _sci_signature(sci_value)
    if sig_target is None:
        return []
    sigfigs = len(sig_target[0])
    out: list[str] = []
    for canon in _clean_candidates(candidates):
        formatted = f"{int(canon):.{sigfigs - 1}E}"  # e.g. "1.00258E+12"
        if _sci_signature(formatted) == sig_target:
            out.append(canon)
    return out


# --------------------------------------------------------------------------
# Single-value recovery
# --------------------------------------------------------------------------

@dataclass
class Recovery:
    status: str                       # CANONICAL | RECOVERED | AMBIGUOUS | SKIPPED
    account: Optional[str]            # canonical 13-digit account when import-able, else None
    reason: str = ""
    raw: str = ""
    consistent: list = field(default_factory=list)  # candidates consistent with a sci value

    @property
    def importable(self) -> bool:
        return self.status in IMPORTED_STATUSES


def recover_account(raw, candidates: Optional[Sequence] = None) -> Recovery:
    """Canonicalize one operator-supplied account value at the import boundary.

    * scientific notation -> recover against *candidates* (exactly-one rule);
    * plain canonical digits -> preserved (zero-padded to 13);
    * anything else -> skipped.
    Never treats a scientific-notation value as canonical, never guesses.
    """
    s = "" if raw is None else str(raw).strip()
    candidates = list(candidates or ())

    if not s:
        return Recovery(SKIPPED, None, "empty account value", s)

    if is_scientific(s):
        consistent = consistent_candidates(s, candidates)
        if len(consistent) == 1:
            return Recovery(RECOVERED, consistent[0],
                            f"recovered from scientific notation {s!r} via the single "
                            f"consistent legal-index candidate", s, consistent)
        if not consistent:
            return Recovery(AMBIGUOUS, None,
                            f"scientific-notation value {s!r} matched no candidate "
                            f"account — manual account confirmation required", s, consistent)
        return Recovery(AMBIGUOUS, None,
                        f"scientific-notation value {s!r} is consistent with "
                        f"{len(consistent)} candidate accounts {consistent} — manual "
                        f"account confirmation required", s, consistent)

    canon = canonical_account(s)
    if canon is not None:
        return Recovery(CANONICAL, canon,
                        "already a canonical account string; preserved", s)

    return Recovery(SKIPPED, None,
                    f"account value {s!r} is neither a canonical account nor recognizable "
                    f"scientific notation", s)


# --------------------------------------------------------------------------
# CSV / row import
# --------------------------------------------------------------------------

DEFAULT_ACCOUNT_FIELDS = ("expected_acct", "candidate_acct")
DEFAULT_CANDIDATE_FIELDS = ("candidate_accounts", "legal_candidates", "candidate_accts")
DEFAULT_LEGAL_KEY_FIELDS = ("subdivision", "lot", "block")

# A legal-index lookup: (subdivision, lot, block) -> iterable of candidate accounts.
LegalLookup = Callable[[str, str, str], Iterable[str]]


@dataclass
class RowResult:
    index: int
    recovery: Recovery
    row: dict = field(default_factory=dict)


@dataclass
class ImportReport:
    imported: list = field(default_factory=list)         # RowResult (CANONICAL or RECOVERED)
    recovered: list = field(default_factory=list)         # RowResult (RECOVERED only)
    canonical: list = field(default_factory=list)         # RowResult (already-canonical)
    needs_manual: list = field(default_factory=list)      # RowResult (AMBIGUOUS)
    skipped: list = field(default_factory=list)           # RowResult (SKIPPED)

    # counts requested by the operator report
    @property
    def recovered_count(self) -> int:
        return len(self.recovered)

    @property
    def ambiguous_count(self) -> int:
        return len(self.needs_manual)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def imported_count(self) -> int:
        return len(self.imported)

    def summary(self) -> dict:
        return {
            "imported": self.imported_count,
            "recovered_scientific": self.recovered_count,
            "already_canonical": len(self.canonical),
            "ambiguous_needs_manual": self.ambiguous_count,
            "skipped": self.skipped_count,
            "total": (self.imported_count + self.ambiguous_count + self.skipped_count),
        }


def _first_present(row: dict, fields: Sequence[str]) -> str:
    for f in fields:
        if f in row and str(row.get(f) or "").strip():
            return str(row[f]).strip()
    return ""


def _row_candidates(row: dict, legal_lookup: Optional[LegalLookup],
                    candidate_fields: Sequence[str],
                    legal_key_fields: Sequence[str]) -> list[str]:
    """Assemble the *clean* candidate account set for a row.

    Preference order:
      1. an injected legal-index lookup keyed on (subdivision, lot, block) — the
         authoritative, Excel-free source;
      2. otherwise, any candidate-account column(s) present in the row (only the
         non-corrupted, plain-digit values are usable).
    """
    if legal_lookup is not None:
        sub, lot, block = (str(row.get(k, "") or "").strip() for k in legal_key_fields)
        try:
            return _clean_candidates(legal_lookup(sub, lot, block))
        except Exception:
            return []
    raw: list[str] = []
    for f in candidate_fields:
        if f in row and row.get(f):
            raw.extend(re.split(r"[^0-9A-Za-z.+-]+", str(row[f])))
    return _clean_candidates(raw)


def import_reviewed_rows(
    rows: Iterable[dict],
    *,
    account_fields: Sequence[str] = DEFAULT_ACCOUNT_FIELDS,
    legal_lookup: Optional[LegalLookup] = None,
    candidate_fields: Sequence[str] = DEFAULT_CANDIDATE_FIELDS,
    legal_key_fields: Sequence[str] = DEFAULT_LEGAL_KEY_FIELDS,
) -> ImportReport:
    """Classify already-parsed reviewed rows. Pure — does not read or write files,
    never mutates the input rows."""
    report = ImportReport()
    for i, row in enumerate(rows):
        raw = _first_present(row, account_fields)
        candidates = _row_candidates(row, legal_lookup, candidate_fields, legal_key_fields)
        rec = recover_account(raw, candidates)
        rr = RowResult(index=i, recovery=rec, row=dict(row))
        if rec.status == CANONICAL:
            report.canonical.append(rr)
            report.imported.append(rr)
        elif rec.status == RECOVERED:
            report.recovered.append(rr)
            report.imported.append(rr)
        elif rec.status == AMBIGUOUS:
            report.needs_manual.append(rr)
        else:
            report.skipped.append(rr)
    return report


def import_reviewed_csv(
    path,
    *,
    account_fields: Sequence[str] = DEFAULT_ACCOUNT_FIELDS,
    legal_lookup: Optional[LegalLookup] = None,
    candidate_fields: Sequence[str] = DEFAULT_CANDIDATE_FIELDS,
    legal_key_fields: Sequence[str] = DEFAULT_LEGAL_KEY_FIELDS,
) -> ImportReport:
    """Read a reviewed CSV **read-only** and classify its rows.

    This function never writes to *path* and never alters the operator's reviewer
    decisions/notes — it only reads. Recovery of the canonical account happens in
    memory; persisting verified cases is a separate, explicit step the caller owns.
    """
    p = Path(path)
    with p.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    return import_reviewed_rows(
        rows,
        account_fields=account_fields,
        legal_lookup=legal_lookup,
        candidate_fields=candidate_fields,
        legal_key_fields=legal_key_fields,
    )
