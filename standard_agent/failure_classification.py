"""Classify baseline outcomes without turning evaluator failures into agent faults."""


def classify_trial(result: dict, task: dict | None = None) -> dict:
    """Return a conservative failure category and attribution flag."""
    if result.get("official_success") is True:
        return {"failure_category": "success", "attributable_to_research": True}

    if result.get("evaluator_status") == "error":
        return {"failure_category": "evaluator_failure", "attributable_to_research": False}

    answer = str(result.get("answer") or "")
    if "reached max steps" in answer.casefold():
        return {"failure_category": "budget_exhausted", "attributable_to_research": True}

    assertions = result.get("evaluator_assertions") or []
    expected = ((task or {}).get("eval") or [{}])[0].get("expected", {})
    schema = ((task or {}).get("eval") or [{}])[0].get("results_schema", {})
    actual = assertions[0].get("actual_normalized") if assertions else None
    actual_data = actual.get("retrieved_data") if isinstance(actual, dict) else None
    expected_data = expected.get("retrieved_data")

    # The runner did not emit the schema requested by the official evaluator.
    # This is a response-format problem, not an LLM reasoning label.
    if actual_data is not None and expected_data is not None:
        item_type = (schema.get("items") or {}).get("type")
        if item_type in {"number", "integer"} and any(isinstance(x, str) for x in actual_data):
            return {"failure_category": "answer_format_failure", "attributable_to_research": False}
        if item_type == "string" and len(actual_data) == 1 and isinstance(actual_data[0], str):
            text = actual_data[0]
            string_expected = [value for value in expected_data if isinstance(value, str)]
            if len(string_expected) == len(expected_data) and len(expected_data) > 1 and all(
                    value.casefold() in text.casefold() for value in string_expected):
                return {"failure_category": "answer_format_failure", "attributable_to_research": False}

    return {"failure_category": "agent_failure", "attributable_to_research": True}
