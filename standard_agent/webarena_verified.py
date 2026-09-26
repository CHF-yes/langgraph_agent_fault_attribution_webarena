"""Adapters for WebArena-Verified task outputs."""

import json
import os
import re
from pathlib import Path

# Canonical dataset location. The runner (``run_baseline``) uses the same
# default, and both honour ``WEBARENA_DATASET``. Keep this the single source of
# truth so an official response is never built from a partially known task.
DEFAULT_DATASET = "/root/webarena-dataset/webarena-verified.json"

# Response files are keyed by task id; cache parsed datasets per (path, mtime)
# so a long matrix does not re-read an 800-task JSON for every trial.
_DATASET_CACHE: dict[tuple[str, float], dict[int, dict]] = {}


class TaskDefinitionNotFound(LookupError):
    """Raised when a task id has no definition in the dataset.

    Callers must not fall back to an empty ``eval`` block: a response built
    without the real contract (task type, expected status, results schema)
    cannot be scored by the official evaluator, and a silent fallback would
    turn a navigation task into a retrieval task.
    """


def dataset_path() -> Path:
    """Return the dataset path, honouring ``WEBARENA_DATASET``."""
    return Path(os.getenv("WEBARENA_DATASET", DEFAULT_DATASET))


def _load_dataset(path: Path) -> dict[int, dict]:
    """Load ``{task_id: task}`` for ``path``, cached by path and mtime."""
    try:
        mtime = path.stat().st_mtime
    except OSError as exc:
        raise TaskDefinitionNotFound(
            f"task dataset not readable at {path}: {exc}"
        ) from exc
    key = (str(path), mtime)
    cached = _DATASET_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskDefinitionNotFound(
            f"task dataset not loadable at {path}: {exc}"
        ) from exc
    table = {int(task["task_id"]): task for task in rows}
    # Keep only the newest dataset in cache.
    _DATASET_CACHE.clear()
    _DATASET_CACHE[key] = table
    return table


def load_task_definition(task_id: int, *, path: str | Path | None = None) -> dict:
    """Return the full dataset entry for ``task_id``.

    The entry is what ``make_agent_response`` needs: the real ``eval`` block
    decides the official task type and results schema.
    """
    dataset = Path(path) if path else dataset_path()
    table = _load_dataset(dataset)
    task = table.get(int(task_id))
    if task is None:
        raise TaskDefinitionNotFound(
            f"task_id {task_id} not found in {dataset} ({len(table)} tasks)"
        )
    return task


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


def write_agent_response(response_path: str | Path, task: dict, *, completed: bool,
                         answer: str) -> Path:
    """Write an official response to an explicit path.

    ``task`` must be the dataset entry (with its real ``eval`` block). Use
    ``load_task_definition`` to obtain it; this writer deliberately does not
    accept a task id alone, so a caller cannot accidentally emit a response
    whose task type came from a placeholder.
    """
    path = Path(response_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(make_agent_response(task, completed=completed, answer=answer),
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def write_task_output(output_dir: str | Path, task: dict, *, completed: bool,
                      answer: str) -> Path:
    """Write the official response file under ``<output>/<task_id>/``."""
    task_dir = Path(output_dir) / str(task["task_id"])
    task_dir.mkdir(parents=True, exist_ok=True)
    return write_agent_response(
        task_dir / "agent_response.json", task, completed=completed, answer=answer
    )
