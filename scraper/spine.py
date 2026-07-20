"""Identity-spine health check (P0).

The spine is the parcels<->owners join that every downstream distress dataset
resolves against. This module measures its health and, critically, compares the
current run to the prior *successful* run so a material regression (e.g. a
truncated owner file that silently halves the join rate) fails loud instead of
publishing a degraded spine.

Metrics:
  - parcel / owner row counts
  - unique parcels (parcel key) and unique accounts (owner key)
  - duplicate-key counts and rates on each side
  - matched / unmatched parcels and the parcel->owner join rate
  - owner-name completeness, parcel-address and owner-address completeness
  - GIS validity (centroid within the county bbox) when geometry is present

Regression policy: for the critical metrics, a drop of more than `max_drop`
versus the prior successful run is a material regression -> FAIL.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Optional

from .arcgis import HARRIS_BBOX

PASS, PARTIAL, FAIL = "PASS", "PARTIAL", "FAIL"

DEFAULT_CONFIG = {
    "parcels_table": "parcels",
    "parcel_key": "hcad_num",
    "parcel_addr": "site_addr_1",
    "owners_table": "owners",
    "owner_key": "acct",
    "owner_name": "mailto",
    "owner_addr": "mail_addr_1",
    "point_lon": "_point_lon",
    "point_lat": "_point_lat",
}

# Critical metrics whose material drop vs the prior success is a hard FAIL.
CRITICAL_METRICS = (
    "unique_parcels", "unique_accounts", "join_rate",
    "owner_name_completeness", "parcel_addr_completeness",
)
DEFAULT_MAX_DROP = 0.10        # >10% drop vs prior success = material regression
DEFAULT_MIN_JOIN_RATE = 0.80   # absolute floor -> warn
DEFAULT_DUP_PARCEL_RATE = 0.01
DEFAULT_DUP_ACCOUNT_RATE = 0.001


@dataclass
class SpineMetrics:
    parcel_rows: int = 0
    owner_rows: int = 0
    unique_parcels: int = 0
    unique_accounts: int = 0
    duplicate_parcel_keys: int = 0
    duplicate_parcel_rate: float = 0.0
    duplicate_account_keys: int = 0
    duplicate_account_rate: float = 0.0
    matched_parcels: int = 0
    unmatched_parcels: int = 0
    join_rate: float = 0.0
    owner_name_completeness: float = 0.0
    parcel_addr_completeness: float = 0.0
    owner_addr_completeness: float = 0.0
    gis_validity: Optional[float] = None
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SpineReport:
    status: str
    metrics: SpineMetrics
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"status": self.status, "metrics": self.metrics.to_dict(),
                "reasons": self.reasons}


def _table_exists(conn, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def _count(conn, sql, params=()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _nonempty_rate(conn, table, col, total) -> float:
    if total == 0:
        return 0.0
    n = _count(conn,
               f'SELECT COUNT(*) FROM "{table}" '
               f'WHERE "{col}" IS NOT NULL AND TRIM(CAST("{col}" AS TEXT)) <> \'\'')
    return n / total


def compute_spine_metrics(conn, cfg: Optional[dict] = None) -> SpineMetrics:
    cfg = {**DEFAULT_CONFIG, **(cfg or {})}
    m = SpineMetrics()
    pt, ot = cfg["parcels_table"], cfg["owners_table"]
    pk, ok = cfg["parcel_key"], cfg["owner_key"]

    if not _table_exists(conn, pt):
        m.warnings.append(f"missing parcels table '{pt}'")
        return m
    if not _table_exists(conn, ot):
        m.warnings.append(f"missing owners table '{ot}'")
        return m

    pcols, ocols = _columns(conn, pt), _columns(conn, ot)
    if pk not in pcols:
        m.warnings.append(f"missing parcel key '{pk}'")
        return m
    if ok not in ocols:
        m.warnings.append(f"missing owner key '{ok}'")
        return m

    m.parcel_rows = _count(conn, f'SELECT COUNT(*) FROM "{pt}"')
    m.owner_rows = _count(conn, f'SELECT COUNT(*) FROM "{ot}"')

    parcel_nonnull = _count(conn, f'SELECT COUNT(*) FROM "{pt}" WHERE "{pk}" IS NOT NULL')
    owner_nonnull = _count(conn, f'SELECT COUNT(*) FROM "{ot}" WHERE "{ok}" IS NOT NULL')
    m.unique_parcels = _count(conn, f'SELECT COUNT(DISTINCT "{pk}") FROM "{pt}" WHERE "{pk}" IS NOT NULL')
    m.unique_accounts = _count(conn, f'SELECT COUNT(DISTINCT "{ok}") FROM "{ot}" WHERE "{ok}" IS NOT NULL')

    m.duplicate_parcel_keys = parcel_nonnull - m.unique_parcels
    m.duplicate_parcel_rate = (m.duplicate_parcel_keys / parcel_nonnull) if parcel_nonnull else 0.0
    m.duplicate_account_keys = owner_nonnull - m.unique_accounts
    m.duplicate_account_rate = (m.duplicate_account_keys / owner_nonnull) if owner_nonnull else 0.0

    m.matched_parcels = _count(
        conn,
        f'SELECT COUNT(*) FROM "{pt}" p WHERE p."{pk}" IS NOT NULL AND EXISTS '
        f'(SELECT 1 FROM "{ot}" o WHERE o."{ok}" = p."{pk}")')
    m.unmatched_parcels = parcel_nonnull - m.matched_parcels
    m.join_rate = (m.matched_parcels / parcel_nonnull) if parcel_nonnull else 0.0

    if cfg["owner_name"] in ocols:
        m.owner_name_completeness = _nonempty_rate(conn, ot, cfg["owner_name"], m.owner_rows)
    else:
        m.warnings.append(f"no owner_name column '{cfg['owner_name']}'")
    if cfg["parcel_addr"] in pcols:
        m.parcel_addr_completeness = _nonempty_rate(conn, pt, cfg["parcel_addr"], m.parcel_rows)
    else:
        m.warnings.append(f"no parcel_addr column '{cfg['parcel_addr']}'")
    if cfg["owner_addr"] in ocols:
        m.owner_addr_completeness = _nonempty_rate(conn, ot, cfg["owner_addr"], m.owner_rows)
    else:
        m.warnings.append(f"no owner_addr column '{cfg['owner_addr']}'")

    # GIS validity only if geometry centroid columns exist
    if cfg["point_lon"] in pcols and cfg["point_lat"] in pcols and m.parcel_rows:
        lon, lat = cfg["point_lon"], cfg["point_lat"]
        mnlon, mnlat, mxlon, mxlat = HARRIS_BBOX
        valid = _count(
            conn,
            f'SELECT COUNT(*) FROM "{pt}" WHERE "{lon}" IS NOT NULL AND "{lat}" IS NOT NULL '
            f'AND "{lon}" BETWEEN ? AND ? AND "{lat}" BETWEEN ? AND ? '
            f'AND NOT ("{lon}" = 0 AND "{lat}" = 0)',
            (mnlon, mxlon, mnlat, mxlat))
        m.gis_validity = valid / m.parcel_rows
    return m


def _as_metric_dict(prior) -> Optional[dict]:
    if prior is None:
        return None
    if isinstance(prior, SpineMetrics):
        return prior.to_dict()
    if isinstance(prior, dict):
        # accept either a bare metrics dict or a {"metrics": {...}} wrapper
        return prior.get("metrics", prior)
    return None


def check_spine(conn, prior_metrics=None, cfg: Optional[dict] = None,
                max_drop: float = DEFAULT_MAX_DROP,
                min_join_rate: float = DEFAULT_MIN_JOIN_RATE) -> SpineReport:
    """Compute spine metrics and grade them (with regression vs prior success).

    FAIL on: missing/empty spine, or a material drop (> max_drop) in any
    critical metric vs the prior successful run. PARTIAL on: soft issues
    (join-rate below the absolute floor, elevated duplicate rates, no prior
    baseline to compare, measurement warnings).
    """
    m = compute_spine_metrics(conn, cfg)
    reasons: list = []
    status = PASS

    # hard failures — no spine means nothing downstream can resolve
    if m.parcel_rows == 0:
        return SpineReport(FAIL, m, ["parcels table empty or missing"] + m.warnings)
    if m.owner_rows == 0:
        return SpineReport(FAIL, m, ["owners table empty or missing"] + m.warnings)

    # absolute soft floors
    if m.join_rate < min_join_rate:
        status = PARTIAL
        reasons.append(f"join_rate {m.join_rate:.4f} below floor {min_join_rate}")
    if m.duplicate_parcel_rate > DEFAULT_DUP_PARCEL_RATE:
        status = PARTIAL
        reasons.append(f"parcel duplicate rate {m.duplicate_parcel_rate:.4f} elevated")
    if m.duplicate_account_rate > DEFAULT_DUP_ACCOUNT_RATE:
        status = PARTIAL
        reasons.append(f"account duplicate rate {m.duplicate_account_rate:.4f} elevated")
    for w in m.warnings:
        status = PARTIAL
        reasons.append(w)

    # regression vs prior successful run
    prior = _as_metric_dict(prior_metrics)
    if prior is None:
        if status == PASS:
            reasons.append("no prior successful run to compare (baseline)")
    else:
        cur = m.to_dict()
        for key in CRITICAL_METRICS:
            pv = prior.get(key)
            cv = cur.get(key)
            if isinstance(pv, (int, float)) and pv > 0 and isinstance(cv, (int, float)):
                if cv < pv * (1.0 - max_drop):
                    status = FAIL
                    reasons.append(
                        f"material regression in {key}: {cv} vs prior {pv} "
                        f"(>{int(max_drop*100)}% drop)")

    return SpineReport(status, m, reasons)
