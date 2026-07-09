"""Optional: use Claude to adjudicate messy name matches against the database.

The local matcher (names.py) handles exact/token/fuzzy cases. Claude is useful
for the leftovers a string metric can't judge: nicknames (BILL vs WILLIAM),
trusts/LLCs that embed a family name, misspellings across word boundaries, etc.

Flow per input name:
  1. pull candidate rows from SQLite with the same LIKE prefilter names.py uses
  2. send the name + candidates to Claude, constrained to a JSON schema
  3. return the accepted matches with Claude's confidence and reasoning

Requires: pip install anthropic, and ANTHROPIC_API_KEY in the environment
(or an `ant auth login` profile).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from .names import _candidate_rows

MODEL = "claude-opus-4-8"

_SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_index": {"type": "integer"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "reason": {"type": "string"},
                },
                "required": ["candidate_index", "confidence", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["matches"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You match a target person or entity name against candidate property-owner "
    "records from the Harris County appraisal database. Owner names are often "
    "LAST FIRST MIDDLE order, may include spouses (SMITH JOHN & MARY), trusts, "
    "estates, or LLCs containing a family name. Return only candidates that "
    "plausibly refer to the same person/entity as the target. Be conservative: "
    "a shared surname alone is not a match unless the rest is consistent."
)


def claude_match_name(
    conn: sqlite3.Connection,
    query: str,
    table: str,
    field: str,
    max_candidates: int = 40,
    model: str = MODEL,
    client=None,
) -> list[dict]:
    import anthropic

    client = client or anthropic.Anthropic()
    rows = _candidate_rows(conn, table, field, query, cap=max_candidates)
    if not rows:
        return []

    candidates = [
        {"index": i, "owner": r.get(field), "row": {k: v for k, v in list(r.items())[:8]}}
        for i, r in enumerate(rows)
    ]
    prompt = (
        f"Target name: {query!r}\n\nCandidates:\n"
        + "\n".join(f"[{c['index']}] {c['owner']}" for c in candidates)
        + "\n\nReturn the candidate indexes that match the target."
    )

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=_SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        return []
    text = next((b.text for b in response.content if b.type == "text"), "{}")
    decisions = json.loads(text).get("matches", [])

    out = []
    for d in decisions:
        idx = d.get("candidate_index")
        if isinstance(idx, int) and 0 <= idx < len(rows):
            out.append(
                {
                    "query": query,
                    "matched_value": rows[idx].get(field),
                    "confidence": d.get("confidence"),
                    "reason": d.get("reason"),
                    "row": rows[idx],
                }
            )
    return out
