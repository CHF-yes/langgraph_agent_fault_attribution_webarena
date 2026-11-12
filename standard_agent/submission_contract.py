"""Schema-driven final-answer validation shared by both agent architectures.

The official WebArena-Verified evaluator scores ``retrieved_data`` against the
task's ``results_schema``.  A prose answer in an array- or object-schema task
(historically task 231) is therefore unscoreable even when the content is
correct.  This module validates only the *shape* of the answer -- never the
expected value -- so the agent can repair its submission before the trial ends.

Enablement is explicit: the caller must set ``SUBMISSION_CONTRACT=1`` (the
baseline arm leaves it off).  The module itself is dependency-free.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

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


def _type_ok(value: Any, expected: Any) -> bool:
    expected_types = expected if isinstance(expected, list) else [expected]
    for name in expected_types:
        if name == "null" and value is None:
            return True
        if name == "array" and isinstance(value, list):
            return True
        if name == "object" and isinstance(value, dict):
            return True
        if name == "string" and isinstance(value, str):
            return True
        if name == "boolean" and isinstance(value, bool):
            return True
        if name == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if name == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
    return False


PLURAL_TYPES = {
    "string": "strings", "number": "numbers", "integer": "integers",
    "object": "objects", "boolean": "booleans", "array": "arrays",
}


def _describe_type(schema: dict | None) -> str:
    if not schema:
        return "a value"
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return " or ".join(str(item) for item in schema_type)
    if schema_type == "array":
        item = (schema.get("items") or {}).get("type")
        return f"array of {PLURAL_TYPES.get(str(item), item or 'values')}"
    return str(schema_type or "value")


def _validate(value: Any, schema: dict | None, path: str, errors: list[str]) -> None:
    if not isinstance(schema, dict) or not schema:
        return
    expected = schema.get("type")
    if expected is not None and not _type_ok(value, expected):
        errors.append(f"{path} must be {_describe_type(schema)}, got {type(value).__name__}")
        return
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        required = schema.get("required") or list(properties)
        for key in required:
            if key not in value:
                errors.append(f"{path}.{key} is required")
        for key, child in properties.items():
            if key in value:
                _validate(value[key], child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _validate(item, items, f"{path}[{index}]", errors)


def _schema_keys(schema: dict | None) -> list[str]:
    if not isinstance(schema, dict):
        return []
    if schema.get("type") == "object":
        return list((schema.get("properties") or {}).keys())
    if schema.get("type") == "array":
        items = schema.get("items") or {}
        if isinstance(items, dict) and items.get("type") == "object":
            return list((items.get("properties") or {}).keys())
    return []


def validate_submission(answer: str, *, results_schema: dict | None,
                        task_type: str | None) -> SubmissionResult:
    """Check only the public output shape of a final answer.

    ``results_schema`` is the task's dataset schema, which contains no expected
    values.  Return a canonical JSON string on success so the response writer
    and the score binding see exactly the structure that was validated.
    """
    task_type = str(task_type or "RETRIEVE").upper()
    if task_type != "RETRIEVE":
        return SubmissionResult(True, str(answer or ""))

    schema_type = str((results_schema or {}).get("type") or "").casefold()
    if schema_type == "null":
        if _is_not_found(str(answer or "")):
            return SubmissionResult(True, str(answer or ""))
        parsed, parse_error = parse_answer(answer)
        if parse_error:
            return SubmissionResult(False, str(answer or ""),
                                    "this task expects no result; state that no matching result was found")
        if parsed is None:
            return SubmissionResult(True, "null")
        return SubmissionResult(False, str(answer or ""),
                                "this task expects no result; state that no matching result was found")

    parsed, parse_error = parse_answer(answer)
    if parse_error:
        expected = _describe_type(results_schema)
        return SubmissionResult(
            False, str(answer or ""),
            f"expected a JSON {expected} but {parse_error}. Call stop again with only valid JSON",
        )
    errors: list[str] = []
    _validate(parsed, results_schema, "answer", errors)
    if errors:
        expected = _describe_type(results_schema)
        keys = _schema_keys(results_schema)
        key_hint = f" with keys {', '.join(keys)}" if keys else ""
        return SubmissionResult(
            False, str(answer or ""),
            f"expected a JSON {expected}{key_hint}; problems: {'; '.join(errors[:4])}. "
            "Call stop again with only valid JSON",
        )
    normalized = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    return SubmissionResult(True, normalized)


def contract_context(results_schema: dict | None, task_type: str | None) -> str:
    """Short, schema-specific instruction for the agent prompts.

    This is intentionally shape-only and contains no expected values.
    """
    if str(task_type or "RETRIEVE").upper() != "RETRIEVE" or not results_schema:
        return ""
    schema_type = str(results_schema.get("type") or "").casefold()
    keys = _schema_keys(results_schema)
    if schema_type == "null":
        return (
            "\n## Official Response Schema\n"
            "This task expects NO matching result. Call stop with an explicit "
            "statement that no matching result was found; leave retrieved_data null.\n"
        )
    if schema_type == "array":
        items = results_schema.get("items") or {}
        item_type = str((items or {}).get("type") or "").casefold()
        if item_type == "object":
            described = f"objects with keys: {', '.join(keys)}" if keys else "objects"
            return (
                "\n## Official Response Schema\n"
                f"The official scoring contract requires a JSON array of {described}. "
                "Use the exact requested key names and JSON types for every field. "
                "Put ONLY the JSON array in stop(answer); no prose or Markdown.\n"
            )
        described = PLURAL_TYPES.get(item_type, item_type or "values")
        return (
            "\n## Official Response Schema\n"
            f"The official scoring contract requires a JSON array of {described}. "
            "Put ONLY the JSON array in stop(answer); no prose or Markdown. "
            "If there are multiple matches, include every match as a separate element.\n"
        )
    if schema_type == "object":
        described = f"keys: {', '.join(keys)}" if keys else "the requested keys"
        return (
            "\n## Official Response Schema\n"
            f"The official scoring contract requires a JSON object with {described}. "
            "Use JSON numbers for numeric fields and put ONLY the JSON object in "
            "stop(answer); no prose or Markdown.\n"
        )
    return (
        "\n## Official Response Schema\n"
        f"The official scoring contract requires a JSON {schema_type or 'value'}. "
        "Put ONLY that JSON value in stop(answer); no prose or Markdown.\n"
    )
