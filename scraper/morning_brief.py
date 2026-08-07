"""Aurora Morning Brief v1 — a deterministic, read-only attention filter.

Aurora answers exactly one question: *"What should Tamika do today?"* — and it
answers it by rendering the systems that already exist. It invents nothing.

This module is a FAITHFUL RENDERER, not a new engine. It creates no scores, no
distress model, no market prediction, no recommendation layer. Every line it
prints is traceable to an existing source, and every section degrades to an
explicit "not yet available" state (with the reason) when its source is not
present. That is the whole point: if 1,000 records changed overnight but only a
handful need a human, the brief shows the handful — and stays silent about the
rest instead of manufacturing activity.

Sections and their ONLY data sources:

  1. CALL TODAY        existing acquisition ranking (a `ranked_leads` table /
                       injected ranking). Absent today -> honest empty state.
  2. REVIEW TODAY      identity resolution: parcels the key-join left unmatched
                       whose owner name resolves to exactly ONE owner record
                       (scraper.spine + scraper.names). A human confirmation
                       here lifts the join rate = promotion/regression evidence.
  3. URGENT CHANGES    existing events only: the nightly run-manifest timeline
                       and spine regression reasons (scraper.monitoring +
                       scraper.spine). New-since-last-brief is diffed against a
                       tiny persisted state file. No invented urgency.
  4. NEIGHBORHOOD      market/ZIP trend data. None exists -> the brief shows
                       exactly "Market trend intelligence not yet available".
  5. TODAY'S FOCUS     one action target derived from the sections above, to
                       reduce decision fatigue to a single line.

Design: deterministic (stable ordering, injectable `now`), offline, no network,
no writes unless the caller asks to persist "seen" state. Text and JSON output.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from . import monitoring, names
from .spine import DEFAULT_CONFIG as SPINE_CFG
from .spine import compute_spine_metrics

# ---- priority vocabulary (drives color; color only aids prioritization) ----
URGENT = "urgent"   # time-sensitive / blocking
REVIEW = "review"   # a human decision is worth making
CALL = "call"       # a qualified lead to contact
OK = "ok"           # healthy / nothing needed
MUTED = "muted"     # source not available; informational only

STATE_FILE = "morning_brief_state.json"

# A single strong owner-name candidate we would ask a human to *confirm* — set
# deliberately above the general fuzzy floor so "single candidate" means it.
REVIEW_MATCH_THRESHOLD = 0.90
DEFAULT_REVIEW_CAP = 6          # never flood; show the few that matter
DEFAULT_REVIEW_SCAN_BUDGET = 4000  # bound the identity scan for a fast brief
STALE_SUCCESS_HOURS = 36.0      # last publication older than this = watch it


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------

@dataclass
class BriefItem:
    """One actionable line. `why` is mandatory — nothing appears unexplained."""
    title: str
    why: str
    priority: str = REVIEW
    subtitle: str = ""
    fields: dict = field(default_factory=dict)
    new_since_last_brief: bool = False

    def to_dict(self) -> dict:
        return {
            "title": self.title, "subtitle": self.subtitle, "why": self.why,
            "priority": self.priority, "fields": self.fields,
            "new_since_last_brief": self.new_since_last_brief,
        }


@dataclass
class Section:
    key: str
    heading: str
    available: bool
    reason: str                       # why-empty / why-unavailable explanation
    items: list = field(default_factory=list)   # list[BriefItem]

    @property
    def actionable(self) -> int:
        return len(self.items)

    def to_dict(self) -> dict:
        return {
            "key": self.key, "heading": self.heading,
            "available": self.available, "reason": self.reason,
            "items": [i.to_dict() for i in self.items],
        }


@dataclass
class MorningBrief:
    generated_at: str
    county: str
    sections: list = field(default_factory=list)     # list[Section]
    focus: str = ""
    actionable_count: int = 0
    records_scanned: int = 0
    state_fingerprint: dict = field(default_factory=dict)

    def section(self, key: str) -> Optional[Section]:
        return next((s for s in self.sections if s.key == key), None)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at, "county": self.county,
            "focus": self.focus, "actionable_count": self.actionable_count,
            "records_scanned": self.records_scanned,
            "sections": [s.to_dict() for s in self.sections],
        }


# --------------------------------------------------------------------------
# Small store helpers (read-only)
# --------------------------------------------------------------------------

def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> list:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def _count(conn: sqlite3.Connection, sql: str, params=()) -> int:
    return conn.execute(sql, params).fetchone()[0]


# --------------------------------------------------------------------------
# 1. CALL TODAY  —  source: existing acquisition ranking (absent today)
# --------------------------------------------------------------------------

def _load_ranking(conn: Optional[sqlite3.Connection]) -> Optional[list]:
    """Read an existing `ranked_leads` table if the acquisition layer produced
    one. Returns None when no ranking source exists — the brief then says so
    rather than inventing leads. The expected (optional) columns are all
    already-produced values; this function computes nothing.
    """
    if conn is None or not _table_exists(conn, "ranked_leads"):
        return None
    cols = _columns(conn, "ranked_leads")
    order = "rank" if "rank" in cols else ("score" if "score" in cols else None)
    q = 'SELECT * FROM "ranked_leads"'
    if order:
        q += f' ORDER BY "{order}"' + ("" if order == "rank" else " DESC")
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(q).fetchall()]
    finally:
        conn.row_factory = None
    return rows


def section_call_today(conn: Optional[sqlite3.Connection],
                       ranking: Optional[Iterable[dict]] = None,
                       cap: int = 5) -> Section:
    if ranking is None:
        ranking = _load_ranking(conn)
    if ranking is None:
        return Section(
            "call_today", "CALL TODAY", available=False,
            reason=("Acquisition ranking layer not present — no scored leads to "
                    "surface. Nothing is fabricated here; this section fills in "
                    "the moment a `ranked_leads` source exists."))
    rows = list(ranking)[:cap]
    items = []
    for r in rows:
        addr = r.get("address") or r.get("site_addr_1") or r.get("parcel") or "(address n/a)"
        owner = r.get("owner") or r.get("owner_name")
        score = r.get("score")
        signals = r.get("distress_signals") or r.get("signals") or []
        if isinstance(signals, str):
            signals = [s.strip() for s in signals.split(",") if s.strip()]
        strategy = r.get("strategy") or r.get("recommended_strategy")
        why = r.get("why") or r.get("reason") or "surfaced by acquisition ranking"
        items.append(BriefItem(
            title=str(addr),
            subtitle=(f"Owner: {owner}" if owner else ""),
            why=str(why), priority=CALL,
            fields={k: v for k, v in {
                "score": score, "distress_signals": signals,
                "strategy": strategy, "parcel": r.get("parcel") or r.get("hcad_num"),
            }.items() if v not in (None, [], "")}))
    reason = "" if items else "Acquisition ranking present but returned no leads today."
    return Section("call_today", "CALL TODAY", available=True, reason=reason, items=items)


# --------------------------------------------------------------------------
# 2. REVIEW TODAY  —  source: identity resolution (spine + names)
# --------------------------------------------------------------------------

def _iter_unmatched_named_parcels(conn, cfg, owner_name_col, budget):
    """Parcels the key-join left unmatched that DO carry an owner name, ordered
    by parcel key for determinism, capped by `budget`. These are the only
    parcels a name-based identity confirmation can help.
    """
    pt, ot = cfg["parcels_table"], cfg["owners_table"]
    pk, ok = cfg["parcel_key"], cfg["owner_key"]
    sql = (
        f'SELECT p."{pk}" AS pk, p."{owner_name_col}" AS pname, '
        f'p."{cfg["parcel_addr"]}" AS paddr '
        f'FROM "{pt}" p WHERE p."{pk}" IS NOT NULL '
        f'AND p."{owner_name_col}" IS NOT NULL '
        f'AND TRIM(CAST(p."{owner_name_col}" AS TEXT)) <> \'\' '
        f'AND NOT EXISTS (SELECT 1 FROM "{ot}" o WHERE o."{ok}" = p."{pk}") '
        f'ORDER BY p."{pk}" LIMIT {int(budget)}'
    )
    return conn.execute(sql).fetchall()


def section_review_today(conn: Optional[sqlite3.Connection],
                         cfg: Optional[dict] = None,
                         cap: int = DEFAULT_REVIEW_CAP,
                         scan_budget: int = DEFAULT_REVIEW_SCAN_BUDGET) -> Section:
    """Identity confirmations worth a human today: an unmatched parcel whose
    owner name resolves to exactly ONE owner record. Confirming it lifts the
    join rate, which is the platform's promotion/regression evidence.
    """
    cfg = {**SPINE_CFG, **(cfg or {})}
    key = "review_today"
    heading = "REVIEW TODAY"

    if conn is None:
        return Section(key, heading, False,
                       "No store connected — identity resolution unavailable.")
    pt, ot = cfg["parcels_table"], cfg["owners_table"]
    if not _table_exists(conn, pt) or not _table_exists(conn, ot):
        return Section(key, heading, False,
                       "Parcels/owners not loaded yet — identity resolution has "
                       "nothing to reconcile.")

    pcols = _columns(conn, pt)
    # the parcel's own owner-name column is what we resolve against owners
    owner_name_col = cfg.get("parcel_owner_name")
    if not owner_name_col or owner_name_col not in pcols:
        from .db import guess_owner_column
        owner_name_col = guess_owner_column(conn, pt)
    if not owner_name_col:
        return Section(key, heading, False,
                       "Parcels carry no owner-name field to resolve — "
                       "single-candidate identity review not possible on this schema.")

    onc = cfg["owner_name"]
    if not _table_exists(conn, ot) or onc not in _columns(conn, ot):
        return Section(key, heading, False,
                       f"Owners table has no '{onc}' name column to match against.")

    candidates = _iter_unmatched_named_parcels(conn, cfg, owner_name_col, scan_budget)
    prior_factory = conn.row_factory
    items, single, ambiguous, no_match = [], 0, 0, 0
    try:
        for row in candidates:
            pk_val, pname, paddr = row[0], row[1], row[2]
            matches = names.match_name(conn, str(pname), ot, onc,
                                       threshold=REVIEW_MATCH_THRESHOLD,
                                       max_results=2)
            if len(matches) == 1:
                single += 1
                m = matches[0]
                if len(items) < cap:
                    items.append(BriefItem(
                        title=f"Parcel {pk_val}",
                        subtitle=(str(paddr) if paddr else ""),
                        priority=REVIEW,
                        why=(f"Owner '{pname}' resolves to a single owner record "
                             f"'{m.matched_value}' ({m.score:.2f} {m.method}) but the "
                             f"key-join missed it — confirm to lift the join rate."),
                        fields={"parcel": pk_val,
                                "parcel_owner_name": pname,
                                "candidate_owner": m.matched_value,
                                "candidate_acct": m.row.get(cfg["owner_key"]),
                                "match_score": m.score, "match_method": m.method}))
            elif len(matches) >= 2:
                ambiguous += 1
            else:
                no_match += 1
    finally:
        conn.row_factory = prior_factory

    scanned = len(candidates)
    if single == 0:
        reason_bits = [f"No single-candidate confirmations among {scanned} "
                       f"unmatched named parcel(s) scanned"]
    else:
        reason_bits = [f"{single} single-candidate confirmation(s) found"]
    if ambiguous:
        reason_bits.append(f"{ambiguous} ambiguous (multiple candidates, deferred)")
    if no_match:
        reason_bits.append(f"{no_match} with no confident candidate (skipped)")
    if scanned >= scan_budget:
        reason_bits.append(f"scan capped at {scan_budget} unmatched parcels")
    reason = "; ".join(reason_bits) + "."
    sec = Section(key, heading, available=True, reason=reason, items=items)
    sec.__dict__["_scanned"] = scanned  # carried for records-scanned accounting
    sec.__dict__["_single_total"] = single
    return sec


# --------------------------------------------------------------------------
# 3. URGENT CHANGES  —  source: existing events (manifests + spine regression)
# --------------------------------------------------------------------------

def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def section_urgent(work_dir: Optional[str], now: datetime,
                   prev_state: Optional[dict] = None) -> Section:
    """Only genuinely time-sensitive items that EXISTING events already support:
    a failed/degraded nightly run, a spine regression reason, or a publication
    that has gone stale. Foreclosure/auction dates and ranking movement are NOT
    invented — those sources do not exist yet.
    """
    key, heading = "urgent_changes", "URGENT CHANGES"
    prev_state = prev_state or {}
    seen_bad = set(prev_state.get("failed_run_ids", []))

    if not work_dir or not Path(work_dir).exists():
        return Section(key, heading, False,
                       "No run history yet — the nightly pipeline has produced no "
                       "events to watch. (No foreclosure/auction feed exists to "
                       "draw dates from either.)")

    attempt = monitoring.latest_attempt(work_dir)
    success = monitoring.latest_success(work_dir)
    if not attempt and not success:
        return Section(key, heading, False,
                       "No run manifests found — nothing time-sensitive to report.")

    items = []
    # (a) latest attempt failed or degraded -> the pipeline needs attention now
    if attempt and attempt.get("status") in ("FAIL", "PARTIAL"):
        rid = attempt.get("run_id")
        reasons = attempt.get("reasons") or []
        items.append(BriefItem(
            title=f"Nightly run {attempt.get('status')}",
            subtitle=str(rid or ""),
            priority=URGENT,
            why=("; ".join(reasons) if reasons else "run did not pass"),
            new_since_last_brief=bool(rid) and rid not in seen_bad,
            fields={"run_id": rid, "status": attempt.get("status"),
                    "published": attempt.get("published")}))

    # (b) publication gone stale (data not refreshing) — a real, dated event
    succ_dt = _parse_iso(success.get("ended_at")) if success else None
    if succ_dt is not None:
        age_h = (now - succ_dt).total_seconds() / 3600.0
        if age_h > STALE_SUCCESS_HOURS:
            items.append(BriefItem(
                title="Spine publication is stale",
                subtitle=f"last good publish {age_h:.0f}h ago",
                priority=URGENT,
                why=(f"Last successful publication was {age_h:.0f}h ago "
                     f"(> {STALE_SUCCESS_HOURS:.0f}h). Downstream data is aging."),
                fields={"last_success_run": success.get("run_id"),
                        "age_hours": round(age_h, 1)}))
    elif attempt and not success:
        items.append(BriefItem(
            title="No successful publication yet",
            subtitle="", priority=URGENT,
            why="Runs have been attempted but none has published a spine.",
            fields={"latest_attempt": attempt.get("run_id")}))

    # (c) consecutive-failure streak since last success
    streak = monitoring.consecutive_failures(work_dir)
    if streak >= 2:
        items.append(BriefItem(
            title=f"{streak} consecutive non-passing runs",
            subtitle="", priority=URGENT,
            why=f"{streak} runs in a row have not published since the last success.",
            fields={"consecutive_failures": streak}))

    reason = "" if items else ("Pipeline healthy and current — no time-sensitive "
                               "events. (Foreclosure/auction dates: no feed exists.)")
    return Section(key, heading, available=True, reason=reason, items=items)


# --------------------------------------------------------------------------
# 4. NEIGHBORHOOD WATCH  —  source: market/ZIP trend (none exists)
# --------------------------------------------------------------------------

def section_neighborhood(conn: Optional[sqlite3.Connection]) -> Section:
    """v1 keeps this minimal and honest: we have no reliable market/ZIP trend
    data, so we say exactly that instead of fabricating movement.
    """
    return Section(
        "neighborhood_watch", "NEIGHBORHOOD WATCH", available=False,
        reason="Market trend intelligence not yet available.")


# --------------------------------------------------------------------------
# 5. TODAY'S FOCUS  —  one action target derived from the sections above
# --------------------------------------------------------------------------

def _focus(sections: dict) -> str:
    urgent = sections["urgent_changes"]
    review = sections["review_today"]
    call = sections["call_today"]

    if urgent.items:
        top = urgent.items[0]
        return f"Resolve the urgent pipeline issue first: {top.title}."
    if call.items:
        return f"Call the top {len(call.items)} qualified lead(s)."
    if review.items:
        total = review.__dict__.get("_single_total", len(review.items))
        shown = len(review.items)
        if total > shown:
            return (f"Confirm {shown} of {total} single-candidate identity "
                    f"match(es) (start with the shown few).")
        return f"Confirm {shown} single-candidate identity match(es)."
    return ("No action required today — no leads to call, no identity "
            "confirmations pending, and the pipeline is healthy.")


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def build_brief(conn: Optional[sqlite3.Connection] = None,
                work_dir: Optional[str] = None,
                *, county: str = "harris",
                ranking: Optional[Iterable[dict]] = None,
                now: Optional[datetime] = None,
                prev_state: Optional[dict] = None,
                review_cap: int = DEFAULT_REVIEW_CAP,
                review_scan_budget: int = DEFAULT_REVIEW_SCAN_BUDGET) -> MorningBrief:
    """Assemble the brief from existing sources. Pure/read-only: it never writes.

    `prev_state` (from `load_state`) powers new-since-last-brief flags. Persist
    the returned brief's `state_fingerprint` with `save_state` to mark items seen.
    """
    now = now or datetime.now(timezone.utc)

    call = section_call_today(conn, ranking=ranking)
    review = section_review_today(conn, cap=review_cap, scan_budget=review_scan_budget)
    urgent = section_urgent(work_dir, now, prev_state=prev_state)
    hood = section_neighborhood(conn)

    by_key = {s.key: s for s in (call, review, urgent, hood)}
    focus = _focus(by_key)
    sections = [call, review, urgent, hood]

    actionable = sum(s.actionable for s in (call, review, urgent))
    scanned = review.__dict__.get("_scanned", 0)

    # fingerprint used to persist "seen" state for next brief's diff
    failed_ids = []
    att = monitoring.latest_attempt(work_dir) if work_dir and Path(work_dir).exists() else None
    if att and att.get("status") in ("FAIL", "PARTIAL") and att.get("run_id"):
        failed_ids.append(att["run_id"])
    fingerprint = {"brief_at": now.isoformat(), "failed_run_ids": failed_ids}

    return MorningBrief(
        generated_at=now.isoformat(), county=county, sections=sections,
        focus=focus, actionable_count=actionable, records_scanned=scanned,
        state_fingerprint=fingerprint)


# --------------------------------------------------------------------------
# State persistence (tiny, opt-in — mirrors monitoring's latest_*.json idea)
# --------------------------------------------------------------------------

def load_state(work_dir: Optional[str]) -> dict:
    if not work_dir:
        return {}
    p = Path(work_dir) / STATE_FILE
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def save_state(work_dir: str, brief: MorningBrief) -> None:
    p = Path(work_dir) / STATE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(brief.state_fingerprint, indent=2))
    tmp.replace(p)


# --------------------------------------------------------------------------
# Rendering (text + JSON). Color aids prioritization only; never decorates.
# --------------------------------------------------------------------------

_ANSI = {URGENT: "\033[31m", REVIEW: "\033[33m", CALL: "\033[36m",
         OK: "\033[32m", MUTED: "\033[2m", "bold": "\033[1m",
         "dim": "\033[2m", "reset": "\033[0m"}


def _paint(text: str, code: str, color: bool) -> str:
    if not color or code not in _ANSI:
        return text
    return f"{_ANSI[code]}{text}{_ANSI['reset']}"


def render_text(brief: MorningBrief, color: bool = False, width: int = 66) -> str:
    L = []
    bar = "─" * width
    L.append(_paint("AURORA · MORNING BRIEF", "bold", color)
             + _paint(f"   {brief.county.title()} · {brief.generated_at[:16].replace('T', ' ')}",
                      "dim", color))
    # headline: protect attention — one number
    n = brief.actionable_count
    head = (f"{n} item(s) need you today"
            + (f"  ·  {brief.records_scanned} record(s) scanned"
               if brief.records_scanned else ""))
    L.append(_paint(head, OK if n == 0 else REVIEW, color))
    L.append(bar)

    for sec in brief.sections:
        L.append(_paint(sec.heading, "bold", color))
        if not sec.items:
            note = sec.reason or ("(nothing)" if sec.available else "(source not available)")
            L.append("  " + _paint(note, MUTED if not sec.available else "dim", color))
        else:
            for it in sec.items:
                tag = "▲" if it.priority == URGENT else ("•" if it.priority == REVIEW else "›")
                line = f"  {tag} {it.title}"
                if it.subtitle:
                    line += _paint(f"  {it.subtitle}", "dim", color)
                if it.new_since_last_brief:
                    line += _paint("  [new]", URGENT, color)
                L.append(_paint(line, it.priority, color) if it.priority == URGENT else line)
                if it.fields.get("score") is not None:
                    L.append(_paint(f"      score {it.fields['score']}"
                                    + (f" · {', '.join(it.fields.get('distress_signals', []))}"
                                       if it.fields.get("distress_signals") else "")
                                    + (f" · strategy: {it.fields['strategy']}"
                                       if it.fields.get("strategy") else ""), "dim", color))
                L.append(_paint(f"      why: {it.why}", "dim", color))
            if sec.reason:
                L.append(_paint(f"  ({sec.reason})", "dim", color))
        L.append("")

    L.append(bar)
    L.append(_paint("TODAY'S FOCUS", "bold", color))
    L.append("  " + _paint(brief.focus, OK if brief.actionable_count == 0 else REVIEW, color))
    return "\n".join(L)
