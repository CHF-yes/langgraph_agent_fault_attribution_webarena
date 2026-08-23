"""Fault taxonomy and trace-level behavior labels for Web Agent experiments."""

FAULT_CATALOG = {
    "web_timeout": {"layer": "environment", "description": "Action latency injection"},
    "web_http_error": {"layer": "environment", "description": "Synthetic 500/503 response"},
    "web_dom_missing": {"layer": "observation", "description": "AX Tree element loss"},
    "web_popup_block": {"layer": "observation", "description": "Blocking popup injection"},
    "gitlab_ci_offline": {"layer": "environment", "description": "GitLab CI unavailable"},
    "gitlab_permission": {"layer": "environment", "description": "GitLab permission denial"},
    "gitlab_conflict": {"layer": "environment", "description": "GitLab merge conflict"},
    "gitlab_quota": {"layer": "environment", "description": "GitLab quota exhaustion"},
    "agent_state_misjudge": {"layer": "observation", "description": "Misleading element label"},
    "agent_param_error": {"layer": "action", "description": "Invalid tool parameter"},
}

BEHAVIORS = (
    "fault_detected", "fault_ignored", "observation_refreshed",
    "alternative_action_selected", "retry_executed", "plan_revised",
    "stale_action_repeated", "premature_stop", "task_abandoned", "recovered",
)

TOLERANCE_LAYERS = (
    "mechanism_level", "rule_level", "prompt_level", "reasoning_level", "no_tolerance",
)


def classify_behavior(*, fault_type: str, injection_log: list[dict],
                      action_history: list[dict], success: bool,
                      completed: bool, architecture: str = "react",
                      replanning_calls: int = 0) -> dict:
    """Classify observable post-fault behavior without asking an LLM to label it."""
    if not injection_log or fault_type == "control":
        return {"behavior_category": "", "tolerance_layer": "", "recovery_steps": 0}

    actions = [str(item.get("action", "")) for item in action_history]
    errors = [bool(item.get("error")) for item in action_history]
    has_error = any(errors)
    action_counts = {action: actions.count(action) for action in set(actions)}
    repeated = any(count > 1 for count in action_counts.values())
    first_fault_step = min(int(item.get("step", 0) or 0) for item in injection_log)
    recovery_steps = max(0, len(action_history) - first_fault_step)

    if not completed:
        behavior = "task_abandoned" if not actions or not success else "premature_stop"
        tolerance = "no_tolerance"
    elif success:
        if architecture == "plan_execute" and replanning_calls > 0:
            behavior, tolerance = "plan_revised", "mechanism_level"
        elif fault_type in {"web_dom_missing", "agent_state_misjudge"} and any(
                action in {"goto", "scroll"} for action in actions):
            behavior, tolerance = "observation_refreshed", "mechanism_level"
        elif has_error and repeated:
            behavior, tolerance = "retry_executed", "rule_level"
        elif has_error:
            behavior, tolerance = "fault_detected", "reasoning_level"
        else:
            behavior, tolerance = "fault_detected", "prompt_level"
    else:
        behavior = "stale_action_repeated" if repeated else "fault_ignored"
        tolerance = "no_tolerance"

    return {
        "behavior_category": behavior,
        "tolerance_layer": tolerance,
        "recovery_steps": recovery_steps if behavior not in {"task_abandoned", "fault_ignored"} else 0,
    }


def catalog_rows() -> list[dict]:
    """Return stable rows for exporting a fault_catalog.csv file."""
    return [
        {"fault_type": name, **metadata}
        for name, metadata in FAULT_CATALOG.items()
    ]
