"""Adapters for WebArena-Verified task outputs."""

import json
import re
from pathlib import Path


def task_type_for(task: dict) -> str:
    """Return the official task type, falling back to retrieval."""
    expected = (task.get("eval") or [{}])[0].get("expected", {})
    return str(expected.get("task_type", "RETRIEVE")).upper()


def _parse_retrieved_data(answer: str, *, results_schema: dict | None = None):
    """Preserve structured JSON and treat unstructured output as one answer."""
    text = str(answer or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return [text]
    return parsed if isinstance(parsed, list) else [parsed]


def _is_not_found_answer(answer: str) -> bool:
    """Recognize an explicit no-match answer for retrieval tasks."""
    text = str(answer or "").casefold().strip()
    markers = (
        "no reviewer", "no matching", "no match", "none found",
        "nothing found", "not found", "no result", "no results",
        "could not find", "couldn't find", "did not find", "didn't find",
        "没有找到", "未找到", "没有匹配", "无匹配",
    )
    return any(marker in text for marker in markers)


def make_agent_response(task: dict, *, completed: bool, answer: str) -> dict:
    """Build a WebArena-Verified ``agent_response.json`` payload."""
    task_type = task_type_for(task)
    results_schema = (task.get("eval") or [{}])[0].get("results_schema")
    # The response status must depend only on the Agent output. Reading the
    # task's expected status here would leak evaluator ground truth.
    not_found = completed and _is_not_found_answer(answer)
    status = "NOT_FOUND_ERROR" if not_found else ("SUCCESS" if completed else "UNKNOWN_ERROR")
    return {
        "task_type": task_type,
        "status": status,
        "retrieved_data": (
            None if not_found else _parse_retrieved_data(answer, results_schema=results_schema)
            if task_type == "RETRIEVE" else None
        ),
        "error_details": (
            str(answer or "No matching result found") if not_found
            else (None if completed else str(answer or "Agent did not complete the task"))
        ),
    }


def write_task_output(output_dir: str | Path, task: dict, *, completed: bool,
                      answer: str) -> Path:
    """Write the official response file under ``<output>/<task_id>/``."""
    task_dir = Path(output_dir) / str(task["task_id"])
    task_dir.mkdir(parents=True, exist_ok=True)
    response_path = task_dir / "agent_response.json"
    response_path.write_text(
        json.dumps(make_agent_response(task, completed=completed, answer=answer),
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return response_path
