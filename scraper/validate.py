"""Validation gates for loaded datasets (P0 spine).

Runs data-quality checks against the SQLite tables produced by the acquisition
step and returns a structured, machine-readable report with an overall verdict:

    PASS     every check ok
    PARTIAL  at least one warn-level check tripped, no failures
    FAIL     at least one fail-level check tripped

This is the reusable gate the nightly runner consults before publishing output.
It is intentionally storage-only (no network), so it is fast and offline-testable.

Check severities:
  - a check configured `severity="fail"` trips FAIL when violated
  - a check configured `severity="warn"` trips PARTIAL when violated

Report shape mirrors the RunManifest concept in docs/acquisition_layer.md so the
nightly runner and monitoring can treat every dataset uniformly.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

PASS = "PASS"
PARTIAL = "PARTIAL"
FAIL = "FAIL"

_OK = "ok"
_WARN = "warn"
_FAIL = "fail"


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str
    observed: Optional[float] = None
    threshold: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "observed": self.observed,
            "threshold": self.threshold,
        }


@dataclass
class ValidationReport:
    table: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(c.status == _FAIL for c in self.checks):
            return FAIL
        if any(c.status == _WARN for c in self.checks):
            return PARTIAL
        return PASS

    @property
    def ok(self) -> bool:
        return self.status == PASS

    def add(self, check: CheckResult) -> None:
        self.checks.append(check)

    def reasons(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if c.status != _OK]

    def to_dict(self) -> dict:
        return {
            "table": self.table,
            "status": self.status,
            "checks": [c.to_dict() for c in self.checks],
            "reasons": self.reasons(),
        }


def _severity(passed: bool, severity: str) -> str:
    if passed:
        return _OK
    return _FAIL if severity == "fail" else _WARN


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def check_row_count(
    conn: sqlite3.Connection,
    table: str,
    min_expected: int,
    max_expected: Optional[int] = None,
    severity: str = "fail",
) -> CheckResult:
    """Row count must fall within [min_expected, max_expected]."""
    n = _row_count(conn, table)
    ok = n >= min_expected and (max_expected is None or n <= max_expected)
    bound = f">={min_expected}" + (f", <={max_expected}" if max_expected is not None else "")
    return CheckResult(
        name="row_count",
        status=_severity(ok, severity),
        detail=f"{n} rows (expected {bound})",
        observed=float(n),
        threshold=float(min_expected),
    )


def check_required_nonnull(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    min_rate: float = 1.0,
    severity: str = "fail",
) -> CheckResult:
    """Fraction of non-null, non-empty values in `column` must be >= min_rate."""
    total = _row_count(conn, table)
    if total == 0:
        return CheckResult("required_nonnull", _severity(False, severity),
                           f"{column}: table is empty", 0.0, min_rate)
    non_null = conn.execute(
        f'SELECT COUNT(*) FROM "{table}" '
        f'WHERE "{column}" IS NOT NULL AND TRIM(CAST("{column}" AS TEXT)) <> \'\''
    ).fetchone()[0]
    rate = non_null / total
    ok = rate >= min_rate
    return CheckResult(
        name="required_nonnull",
        status=_severity(ok, severity),
        detail=f"{column}: {rate:.4f} non-null (need >={min_rate})",
        observed=round(rate, 4),
        threshold=min_rate,
    )


def check_duplicate_rate(
    conn: sqlite3.Connection,
    table: str,
    key_column: str,
    max_rate: float = 0.0,
    severity: str = "warn",
) -> CheckResult:
    """Duplicate fraction on `key_column` must be <= max_rate.

    duplicate_rate = 1 - (distinct non-null keys / non-null rows).
    """
    total = conn.execute(
        f'SELECT COUNT(*) FROM "{table}" WHERE "{key_column}" IS NOT NULL'
    ).fetchone()[0]
    if total == 0:
        return CheckResult("duplicate_rate", _severity(True, severity),
                           f"{key_column}: no non-null keys to check", 0.0, max_rate)
    distinct = conn.execute(
        f'SELECT COUNT(DISTINCT "{key_column}") FROM "{table}" '
        f'WHERE "{key_column}" IS NOT NULL'
    ).fetchone()[0]
    dup_rate = 1.0 - (distinct / total)
    ok = dup_rate <= max_rate
    return CheckResult(
        name="duplicate_rate",
        status=_severity(ok, severity),
        detail=f"{key_column}: {dup_rate:.4f} duplicate (allow <={max_rate})",
        observed=round(dup_rate, 4),
        threshold=max_rate,
    )


def check_freshness(
    conn: sqlite3.Connection,
    table: str,
    max_age_hours: float,
    severity: str = "warn",
    now: Optional[datetime] = None,
) -> CheckResult:
    """`_scrape_meta.updated_at` for `table` must be newer than max_age_hours.

    If there is no provenance row (e.g. bulk loaders that don't write _scrape_meta),
    returns a warn — absence of provenance is itself a data-quality signal.
    """
    now = now or datetime.now(timezone.utc)
    row = conn.execute(
        "SELECT updated_at FROM _scrape_meta WHERE table_name=?", (table,)
    ).fetchone()
    if not row or not row[0]:
        return CheckResult("freshness", _WARN,
                           "no _scrape_meta provenance timestamp", None, max_age_hours)
    try:
        ts = datetime.fromisoformat(str(row[0]))
    except ValueError:
        return CheckResult("freshness", _WARN,
                           f"unparseable timestamp {row[0]!r}", None, max_age_hours)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_hours = (now - ts).total_seconds() / 3600.0
    ok = age_hours <= max_age_hours
    return CheckResult(
        name="freshness",
        status=_severity(ok, severity),
        detail=f"{age_hours:.2f}h old (allow <={max_age_hours}h)",
        observed=round(age_hours, 2),
        threshold=max_age_hours,
    )


def check_join_rate(
    conn: sqlite3.Connection,
    left_table: str,
    left_key: str,
    right_table: str,
    right_key: str,
    min_rate: float,
    severity: str = "warn",
) -> CheckResult:
    """Fraction of `left_table` rows whose key matches a `right_table` key.

    This is the identity-spine health metric: parcels that resolve to an owner
    record (or vice versa). A low join rate signals schema/version drift.
    """
    total = conn.execute(
        f'SELECT COUNT(*) FROM "{left_table}" WHERE "{left_key}" IS NOT NULL'
    ).fetchone()[0]
    if total == 0:
        return CheckResult("join_rate", _severity(False, severity),
                           f"{left_table}.{left_key}: no non-null keys", 0.0, min_rate)
    matched = conn.execute(
        f'SELECT COUNT(*) FROM "{left_table}" l '
        f'WHERE l."{left_key}" IS NOT NULL AND EXISTS '
        f'(SELECT 1 FROM "{right_table}" r WHERE r."{right_key}" = l."{left_key}")'
    ).fetchone()[0]
    rate = matched / total
    ok = rate >= min_rate
    return CheckResult(
        name="join_rate",
        status=_severity(ok, severity),
        detail=f"{left_table}.{left_key}->{right_table}.{right_key}: "
               f"{rate:.4f} matched (need >={min_rate})",
        observed=round(rate, 4),
        threshold=min_rate,
    )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def validate_table(conn: sqlite3.Connection, spec: dict) -> ValidationReport:
    """Run a spec of checks against one table.

    spec = {
      "table": "parcels",
      "row_count": {"min_expected": 1_200_000, "max_expected": 1_800_000},
      "required_nonnull": [{"column": "hcad_num", "min_rate": 0.99}],
      "duplicate_rate": [{"key_column": "hcad_num", "max_rate": 0.01}],
      "freshness": {"max_age_hours": 24},
      "join_rate": [{"left_key": "hcad_num", "right_table": "owners",
                     "right_key": "acct", "min_rate": 0.9}],
    }
    Absent sections are skipped. A missing table yields a single FAIL check.
    """
    table = spec["table"]
    report = ValidationReport(table=table)

    if not _table_exists(conn, table):
        report.add(CheckResult("table_exists", _FAIL, f"table '{table}' not found"))
        return report

    if "row_count" in spec:
        rc = spec["row_count"]
        report.add(check_row_count(conn, table, rc["min_expected"],
                                   rc.get("max_expected"),
                                   rc.get("severity", "fail")))
    for r in spec.get("required_nonnull", []):
        report.add(check_required_nonnull(conn, table, r["column"],
                                          r.get("min_rate", 1.0),
                                          r.get("severity", "fail")))
    for d in spec.get("duplicate_rate", []):
        report.add(check_duplicate_rate(conn, table, d["key_column"],
                                        d.get("max_rate", 0.0),
                                        d.get("severity", "warn")))
    if "freshness" in spec:
        fr = spec["freshness"]
        report.add(check_freshness(conn, table, fr["max_age_hours"],
                                   fr.get("severity", "warn")))
    for j in spec.get("join_rate", []):
        report.add(check_join_rate(conn, table, j["left_key"],
                                   j["right_table"], j["right_key"],
                                   j["min_rate"], j.get("severity", "warn")))
    return report


# Default Harris spine specs. Bounds for parcels/owners are heuristic pending the
# UNVERIFIED true counts noted in county_source_registry/harris/{property,gis}.md;
# adjust once a live sample confirms them.
HARRIS_SPINE_SPECS = [
    {
        "table": "parcels",
        "row_count": {"min_expected": 1_200_000, "max_expected": 1_800_000},
        "required_nonnull": [{"column": "hcad_num", "min_rate": 0.99}],
        "duplicate_rate": [{"key_column": "hcad_num", "max_rate": 0.01, "severity": "warn"}],
        "freshness": {"max_age_hours": 24 * 100},  # parcel fabric changes slowly
        "join_rate": [{"left_key": "hcad_num", "right_table": "owners",
                       "right_key": "acct", "min_rate": 0.9}],
    },
    {
        "table": "owners",
        "row_count": {"min_expected": 1_200_000, "max_expected": 1_800_000},
        "required_nonnull": [
            {"column": "acct", "min_rate": 0.999},
            {"column": "mailto", "min_rate": 0.9, "severity": "warn"},
        ],
        "duplicate_rate": [{"key_column": "acct", "max_rate": 0.001, "severity": "warn"}],
    },
]


def validate_many(conn: sqlite3.Connection, specs: list[dict]) -> list[ValidationReport]:
    return [validate_table(conn, s) for s in specs]


def overall_status(reports: list[ValidationReport]) -> str:
    statuses = {r.status for r in reports}
    if FAIL in statuses:
        return FAIL
    if PARTIAL in statuses:
        return PARTIAL
    return PASS
