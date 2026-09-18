"""Canonicalise the plan-and-execute replanner's ``decision`` field.

Why this module exists
----------------------
``replanner_node`` used to read the model's JSON verbatim::

    decision = decision_data.get("decision", "replan")

and then route on ``decision == "finish"`` / ``decision == "continue"``. Every
other token -- a synonym such as ``"next"``, a differently-cased ``"Finish"``,
a missing key, malformed JSON -- fell through to the function's final
``return``, which asks the graph for a **full replan**
(``replan_required=True`` -> back to ``planner`` in ``core/graph.py``).

That silent fallthrough is a confound for this paper's central claim. A model
that phrases the same intent differently (``"next"`` rather than
``"continue"``) accumulates extra planner + executor rounds, which surfaces as
a longer trajectory and a lower success rate -- i.e. **model phrasing gets
charged to the architecture**. The effect is invisible precisely because the
counterfactual (the old parser) never runs.

This module decouples routing from phrasing while keeping the deviation
visible and countable:

``decision``
    Always canonical: ``continue`` | ``replan`` | ``finish``.
``matched_by``
    ``"exact"`` | ``"alias"`` | ``"default"``.
``raw_value``
    What the model actually emitted, as text (``""`` when absent).
``json_found``
    A JSON object was extracted from the response.
``contract_violation``
    ``True`` unless ``matched_by == "exact"``.

Behaviour change vs. the old parser
-----------------------------------
* ``exact``   -- unchanged.
* ``default`` -- unchanged (both old and new route to replan).
* ``alias``   -- **changed**. These inputs used to trigger an unconditional
  replan; they now route to the intended branch.

Only the ``alias`` row changes any routing decision, and it only ever turns a
spurious replan into the branch the model asked for. Canonical outputs are
untouched, so results already collected with a well-behaved model stay
comparable. ``contract_violation`` then lets run-to-run protocol drift be
reported as a *model* property instead of being silently charged to the
architecture.

The JSON extractor is also hardened: it scans for the first ``{`` that
``json.JSONDecoder.raw_decode`` accepts, instead of the previous greedy
``re.search(r"\\{.*\\}", ...)``. A response like
``{"decision":"next"} ... trailing {"x":1}`` used to make the greedy match
fail (and silently become a replan); it now yields the first object. Again the
change is one-directional -- it can only repair a would-be default replan.

Pure standard library: importable and testable without langgraph/langchain.
"""

import json
import re
from typing import NamedTuple

__all__ = [
    "ALIASES",
    "CANONICAL_DECISIONS",
    "DEFAULT_DECISION",
    "DecisionResult",
    "normalize_decision",
]

CANONICAL_DECISIONS = ("continue", "replan", "finish")

#: Used for every non-canonical input. Matches the old parser's fallback, so
#: existing behaviour is preserved for inputs the old parser also defaulted.
DEFAULT_DECISION = "replan"

#: Synonyms carrying the same intent as a canonical token. Keys are already in
#: normalised form (casefolded, whitespace/hyphens collapsed to ``_``).
ALIASES = {
    # -> continue
    "next": "continue",
    "proceed": "continue",
    "advance": "continue",
    "keep_going": "continue",
    "carry_on": "continue",
    "ongoing": "continue",
    "same_plan": "continue",
    "continue_plan": "continue",
    # -> finish
    "done": "finish",
    "complete": "finish",
    "completed": "finish",
    "finished": "finish",
    "stop": "finish",
    "end": "finish",
    "success": "finish",
    "terminate": "finish",
    "final": "finish",
    # -> replan
    "re_plan": "replan",
    "replan_required": "replan",
    "retry": "replan",
    "revise": "replan",
    "restart": "replan",
    "reset": "replan",
    "new_plan": "replan",
    "redo": "replan",
}


class DecisionResult(NamedTuple):
    """Outcome of normalising one replanner response."""

    decision: str
    matched_by: str
    raw_value: str
    json_found: bool
    contract_violation: bool


def _normalize_key(value) -> str:
    """Casefold and collapse separators so ``"Re-Plan"`` matches ``re_plan``."""
    return re.sub(r"[\s\-]+", "_", str(value).strip().casefold()).strip("_")


def _extract_json_object(raw_text: str):
    """Return the first JSON object found in ``raw_text``, else ``None``.

    Scans candidate ``{`` offsets and keeps the first one that parses as a
    JSON object. Non-greedy by construction, so trailing text or a second
    object does not poison the match the way a greedy regex would.
    """
    text = raw_text or ""
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def normalize_decision(raw_text) -> DecisionResult:
    """Map a replanner response onto a canonical decision.

    Never raises: unparseable input yields :data:`DEFAULT_DECISION` with
    ``matched_by="default"`` and ``contract_violation=True``.
    """
    text = str(raw_text or "")
    data = _extract_json_object(text)
    json_found = data is not None
    value = data.get("decision") if json_found else None

    if value is None:
        return DecisionResult(DEFAULT_DECISION, "default", "", json_found, True)

    raw_value = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    key = _normalize_key(value)

    if key in CANONICAL_DECISIONS:
        return DecisionResult(key, "exact", raw_value, True, False)
    if key in ALIASES:
        return DecisionResult(ALIASES[key], "alias", raw_value, True, True)
    return DecisionResult(DEFAULT_DECISION, "default", raw_value, json_found, True)
