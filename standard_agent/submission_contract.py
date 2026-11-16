"""Retrieval submission convention shared by both agent architectures.

The model must be able to submit what it found without learning the evaluator's
answer.  The dataset ``results_schema`` is derived from ground truth (every
``type: null`` task expects ``retrieved_data: null``), so neither the prompt nor
the validator reads it.  Instead both use one public convention:

- valid JSON is accepted as a non-empty result,
- an explicit "no matching result" statement (or a JSON empty value) is accepted
  as an empty result,
- anything else costs one correction; a second invalid submission ends the trial
  as ``submission_contract_failed``.

Whether a submitted result is correct is decided only by the official evaluator.

Enablement is explicit: the caller must set ``SUBMISSION_CONTRACT=1`` (the
baseline arm leaves it off).  The module itself is dependency-free.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

MAX_SUBMISSION_ATTEMPTS = 2

NOT_FOUND_MARKERS = (
    "no reviewer", "no matching", "no match", "none found",
    "nothing found", "not found", "no result", "no results",
    "could not find", "couldn't find", "did not find", "didn't find",
    "没有找到", "未找到", "没有匹配", "无匹配",
)


@dataclass(frozen=True)
class SubmissionResult:
    ok: bool
    normalized: str
    error: str | None = None


def _is_not_found(answer: str) -> bool:
    text = str(answer or "").casefold()
    return any(marker in text for marker in NOT_FOUND_MARKERS)


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip().startswith("```"):
        lines = lines[1:-1]
    else:
        lines = lines[1:]
    # Drop a language tag such as "json" on the opening fence line.
    if lines and lines[0].strip().casefold() in {"json", "javascript", "js"}:
        lines = lines[1:]
    return "\n".join(lines).strip()


def parse_answer(answer: str) -> tuple[Any, str | None]:
    """Parse a ``stop(answer=...)`` value as JSON, accepting fenced JSON."""
    text = _strip_code_fence(str(answer or ""))
    if not text:
        return None, "the answer is empty"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"the answer is not valid JSON ({exc.msg} at line {exc.lineno})"


def validate_submission(answer: str, *, task_type: str | None) -> SubmissionResult:
    """Check only the public retrieval submission convention.

    No dataset schema, expected value, or task id is consulted, so changing the
    evaluator's empty-result field cannot change what the model sees or whether
    its submission is accepted.
    """
    task_type = str(task_type or "RETRIEVE").upper()
    if task_type != "RETRIEVE":
        return SubmissionResult(True, str(answer or ""))

    text = str(answer or "").strip()
    if not text:
        return SubmissionResult(
            False, "",
            "the answer is empty; submit your findings as JSON or state that no "
            "matching result was found",
        )
    if _is_not_found(text):
        return SubmissionResult(True, text)

    parsed, parse_error = parse_answer(text)
    if parse_error:
        return SubmissionResult(
            False, text,
            f"{parse_error}. Submit only valid JSON, or state explicitly that no "
            "matching result was found",
        )
    if parsed is None or parsed == []:
        return SubmissionResult(True, "No matching result found.")
    return SubmissionResult(
        True, json.dumps(parsed, ensure_ascii=False, separators=(",", ":")))


def contract_context(task_type: str | None) -> str:
    """The same public submission convention for every retrieval task.

    Deliberately independent of the task definition: the prompt cannot reveal
    whether the evaluator expects a non-empty result or an empty one.
    """
    if str(task_type or "RETRIEVE").upper() != "RETRIEVE":
        return ""
    return (
        "\n## Final Answer Format\n"
        "Submit only valid JSON. Report every matching item as an element of a "
        "JSON array; when the task asks for a single object, number, or string, "
        "a one-element array containing it is valid. If the inspection is "
        "exhaustive and finds no matching result, call stop with an explicit "
        "statement that no matching result was found; do not invent values.\n"
    )
