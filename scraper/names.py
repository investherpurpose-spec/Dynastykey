"""Match person/entity names against owner records stored in SQLite.

Matching pipeline (no API calls, runs entirely local):
  1. normalize both sides (uppercase, strip punctuation/suffixes)
  2. exact normalized match
  3. token-set match (handles "SMITH JOHN" vs "JOHN SMITH")
  4. fuzzy ratio via difflib for near-misses (typos, middle initials)
"""

from __future__ import annotations

import difflib
import re
import sqlite3
from dataclasses import dataclass
from typing import Iterable, Optional

SUFFIXES = {"JR", "SR", "II", "III", "IV", "V", "ESQ", "MD", "PHD"}
NOISE_WORDS = {"THE", "OF", "AND", "&", "ET", "AL", "ETAL", "UX", "ET UX"}


def normalize(name: str) -> str:
    up = re.sub(r"[^A-Z0-9 ]+", " ", (name or "").upper())
    tokens = [t for t in up.split() if t and t not in SUFFIXES]
    return " ".join(tokens)


def token_set(name: str) -> frozenset[str]:
    return frozenset(t for t in normalize(name).split() if t not in NOISE_WORDS)


@dataclass
class Match:
    query: str
    matched_value: str
    score: float
    method: str
    row: dict


def score_pair(query: str, candidate: str) -> tuple[float, str]:
    nq, nc = normalize(query), normalize(candidate)
    if not nq or not nc:
        return 0.0, "none"
    if nq == nc:
        return 1.0, "exact"
    tq, tc = token_set(query), token_set(candidate)
    if tq and tq == tc:
        return 0.97, "token_set"
    if tq and (tq <= tc or tc <= tq):
        return 0.9, "token_subset"
    ratio = difflib.SequenceMatcher(None, nq, nc).ratio()
    return ratio, "fuzzy"


def _candidate_rows(
    conn: sqlite3.Connection, table: str, field: str, query: str, cap: int = 5000
) -> list[dict]:
    """Prefilter with LIKE on the rarest tokens so we don't fuzzy-score millions of rows."""
    conn.row_factory = sqlite3.Row
    tokens = sorted(token_set(query), key=len, reverse=True)[:2]
    if not tokens:
        return []
    where = " AND ".join(f'UPPER("{field}") LIKE ?' for _ in tokens)
    params = [f"%{t}%" for t in tokens]
    rows = conn.execute(
        f'SELECT * FROM "{table}" WHERE {where} LIMIT {cap}', params
    ).fetchall()
    if not rows and len(tokens) > 1:  # relax to single-token prefilter
        rows = conn.execute(
            f'SELECT * FROM "{table}" WHERE UPPER("{field}") LIKE ? LIMIT {cap}',
            [f"%{tokens[0]}%"],
        ).fetchall()
    return [dict(r) for r in rows]


def match_name(
    conn: sqlite3.Connection,
    query: str,
    table: str,
    field: str,
    threshold: float = 0.82,
    max_results: int = 10,
) -> list[Match]:
    results: list[Match] = []
    for row in _candidate_rows(conn, table, field, query):
        value = row.get(field) or ""
        score, method = score_pair(query, value)
        if score >= threshold:
            results.append(Match(query, value, round(score, 3), method, row))
    results.sort(key=lambda m: -m.score)
    return results[:max_results]


def match_many(
    conn: sqlite3.Connection,
    queries: Iterable[str],
    table: str,
    field: str,
    threshold: float = 0.82,
) -> list[Match]:
    out: list[Match] = []
    for q in queries:
        q = q.strip()
        if q:
            out.extend(match_name(conn, q, table, field, threshold))
    return out
