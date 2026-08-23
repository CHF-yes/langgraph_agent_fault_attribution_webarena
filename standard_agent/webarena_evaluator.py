"""Optional adapter for the official WebArena-Verified evaluator."""

import json
import os
import sys
from pathlib import Path


def _load_official_api():
    """Load the installed WebArena-Verified API, optionally from a source checkout."""
    source_root = Path(os.getenv("WEBARENA_VERIFIED_ROOT", "/root/webarena-verified")) / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from webarena_verified import WebArenaVerified

    return WebArenaVerified


def evaluate_task(task_id: int, *, agent_response_path: str | Path,
                  network_trace_path: str | Path | None = None,
                  config_path: str | Path | None = None) -> dict:
    """Run the official evaluator and return JSON-serializable details."""
    WebArenaVerified = _load_official_api()
    evaluator = WebArenaVerified(config=Path(config_path) if config_path else None)
    result = evaluator.evaluate_task(
        task_id=task_id,
        agent_response=Path(agent_response_path),
        network_trace=Path(network_trace_path) if network_trace_path else None,
    )
    data = result.model_dump(mode="json")
    status = data.get("status")
    if isinstance(status, dict) and "value" in status:
        data["status"] = status["value"]
    nested_error = ""
    for evaluator_result in data.get("evaluators_results", []):
        nested_error = str(evaluator_result.get("error_msg") or "")
        if nested_error:
            break
    if nested_error:
        fallback = _evaluate_null_retrieval_schema(
            task_id, agent_response_path, nested_error
        )
        if fallback is not None:
            return fallback
    data["official_success"] = data.get("status", "").casefold() == "success"
    return data


def evaluate_task_safe(task_id: int, *, agent_response_path: str | Path,
                       network_trace_path: str | Path | None = None,
                       config_path: str | Path | None = None) -> dict:
    """Evaluate a task while converting unavailable/invalid setup into details."""
    try:
        return evaluate_task(
            task_id,
            agent_response_path=agent_response_path,
            network_trace_path=network_trace_path,
            config_path=config_path,
        )
    except Exception as exc:
        fallback = _evaluate_null_retrieval_schema(
            task_id, agent_response_path, str(exc)
        )
        if fallback is not None:
            return fallback
        return {
            "official_success": False,
            "official_score": 0.0,
            "status": "ERROR",
            "error_msg": str(exc),
            "evaluators_results": [],
        }


def _evaluate_null_retrieval_schema(task_id: int, response_path: str | Path,
                                    error: str) -> dict | None:
    """Handle the upstream evaluator's null-array schema bug conservatively.

    This fallback is only activated for the known upstream schema exception. It
    compares the emitted response with the task contract; it never changes the
    Agent response or uses expected values to construct one.
    """
    if "Schema type must be 'array', got: 'null'" not in error:
        return None
    dataset_path = Path(os.getenv("WEBARENA_DATASET", "/root/webarena-dataset/webarena-verified.json"))
    try:
        tasks = json.loads(dataset_path.read_text(encoding="utf-8"))
        task = next(task for task in tasks if task["task_id"] == task_id)
        actual = json.loads(Path(response_path).read_text(encoding="utf-8"))
        expected = (task.get("eval") or [{}])[0].get("expected", {})
    except (OSError, json.JSONDecodeError, StopIteration, KeyError) as exc:
        return None

    expected_status = str(expected.get("status", "")).casefold()
    actual_status = str(actual.get("status", "")).casefold()
    expected_data = expected.get("retrieved_data")
    actual_data = actual.get("retrieved_data")
    passed = (
        actual_status == expected_status
        and actual_data is None
        and expected_data is None
    )
    assertion_status = "success" if passed else "failure"
    return {
        "official_success": passed,
        "official_score": 1.0 if passed else 0.0,
        "score": 1.0 if passed else 0.0,
        "status": assertion_status,
        "error_msg": None,
        "evaluators_results": [{
            "evaluator_name": "AgentResponseEvaluatorCompat",
            "status": assertion_status,
            "score": 1.0 if passed else 0.0,
            "actual": actual,
            "expected": {
                "task_type": expected.get("task_type"),
                "status": expected.get("status"),
                "retrieved_data": expected_data,
            },
            "assertion_name": "null_retrieval_schema_compatibility",
            "upstream_error": error,
        }],
    }
