"""Shared submission-contract and retrieval-repair regression tests.

These tests lock the improved-arm behavior only:
- schema shape validation (no expected values are used),
- the one-turn ``stop(answer)`` rejection loop in ``run_tools``,
- schema-aware retrieval completeness guidance,
- the baseline arm staying unchanged when the flags are off.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from standard_agent.core import nodes
from standard_agent.environment.webarena_config import get_storage_state_path
from standard_agent.submission_contract import (
    contract_context,
    validate_submission,
)

ARRAY_STRINGS = {"type": "array", "items": {"type": "string"}}
ARRAY_NUMBERS = {"type": "array", "items": {"type": "number"}}
ARRAY_OBJECTS = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "names": {"type": "array", "items": {"type": "string"}},
            "min": {"type": "number", "format": "currency"},
            "max": {"type": "number", "format": "currency"},
        },
    },
}
NULL_SCHEMA = {"type": "null"}


class TestValidateSubmission(unittest.TestCase):
    def test_prose_in_array_schema_is_rejected(self):
        result = validate_submission(
            "Your most recent cancelled order is Order # 000000170.",
            results_schema=ARRAY_STRINGS, task_type="RETRIEVE",
        )
        self.assertFalse(result.ok)
        self.assertIn("JSON array", result.error)

    def test_atomic_array_is_accepted_and_normalized(self):
        result = validate_submission(
            '["000000170"]', results_schema=ARRAY_STRINGS, task_type="RETRIEVE")
        self.assertTrue(result.ok)
        self.assertEqual(result.normalized, '["000000170"]')

    def test_scalar_does_not_satisfy_array_schema(self):
        result = validate_submission(
            '"170"', results_schema=ARRAY_STRINGS, task_type="RETRIEVE")
        self.assertFalse(result.ok)

    def test_numbers_are_type_checked(self):
        self.assertTrue(validate_submission(
            "[187, 183]", results_schema=ARRAY_NUMBERS, task_type="RETRIEVE").ok)
        self.assertFalse(validate_submission(
            '["187"]', results_schema=ARRAY_NUMBERS, task_type="RETRIEVE").ok)

    def test_object_items_require_keys_and_numeric_prices(self):
        good = (
            '{"names": ["A", "B"], "min": 12.99, "max": 14.98}'
        )
        self.assertTrue(validate_submission(
            f"[{good}]", results_schema=ARRAY_OBJECTS, task_type="RETRIEVE").ok)
        missing = '{"names": ["A"], "min": 12.99}'
        self.assertFalse(validate_submission(
            f"[{missing}]", results_schema=ARRAY_OBJECTS, task_type="RETRIEVE").ok)
        string_price = '{"names": ["A"], "min": "12.99", "max": 14.98}'
        self.assertFalse(validate_submission(
            f"[{string_price}]", results_schema=ARRAY_OBJECTS, task_type="RETRIEVE").ok)

    def test_fenced_json_is_accepted(self):
        result = validate_submission(
            '```json\n["170"]\n```',
            results_schema=ARRAY_STRINGS, task_type="RETRIEVE",
        )
        self.assertTrue(result.ok)

    def test_navigate_task_is_not_shape_checked(self):
        result = validate_submission(
            "Opened the requested page.", results_schema=None, task_type="NAVIGATE")
        self.assertTrue(result.ok)

    def test_null_schema_accepts_explicit_not_found(self):
        self.assertTrue(validate_submission(
            "No matching reviewer found.", results_schema=NULL_SCHEMA,
            task_type="RETRIEVE").ok)
        self.assertTrue(validate_submission(
            "null", results_schema=NULL_SCHEMA, task_type="RETRIEVE").ok)
        self.assertFalse(validate_submission(
            "The answer is 3.", results_schema=NULL_SCHEMA,
            task_type="RETRIEVE").ok)


class _FakeToolNode:
    def __init__(self, tools):
        self.tools = tools

    def invoke(self, state):
        tool_calls = state["messages"][-1].tool_calls
        messages = []
        for call in tool_calls:
            answer = call["args"].get("answer", "")
            messages.append(ToolMessage(
                content=f"[OK] 任务完成！答案: {answer}",
                tool_call_id=call["id"], name=call["name"],
            ))
        return {"messages": messages}


def _tool_state(answer: str):
    ai = AIMessage(content="THOUGHT: submit", tool_calls=[{
        "name": "stop", "args": {"answer": answer}, "id": "call_1",
    }])
    return {
        "messages": [HumanMessage(content="task"), ai],
        "step_count": 1, "max_steps": 5, "task": "Get the order number",
        "url": "http://example.test", "architecture": "react",
        "done": False, "answer": "",
        "submission_schema": ARRAY_STRINGS, "submission_task_type": "RETRIEVE",
        "submission_rejections": 0,
    }


class TestRunToolsSubmissionLoop(unittest.TestCase):
    def _run(self, state):
        with patch("langgraph.prebuilt.ToolNode", _FakeToolNode), \
                patch("standard_agent.core.nodes.append_trace"), \
                patch("standard_agent.core.nodes.append_event"):
            return nodes.run_tools(state, {"configurable": {"thread_id": "contract-test"}})

    def test_rejects_prose_and_keeps_task_open(self):
        with patch.dict(os.environ, {"SUBMISSION_CONTRACT": "1"}):
            out = self._run(_tool_state("The order is 170."))
        self.assertFalse(out.get("done", False))
        self.assertEqual(out.get("submission_rejections"), 1)
        self.assertTrue(out["messages"][-1].content.startswith("[Error] Submission rejected"))

    def test_accepts_valid_json_and_finishes(self):
        with patch.dict(os.environ, {"SUBMISSION_CONTRACT": "1"}):
            out = self._run(_tool_state('["170"]'))
        self.assertTrue(out.get("done"))
        self.assertEqual(out.get("answer"), '["170"]')

    def test_baseline_with_flag_off_keeps_old_behavior(self):
        with patch.dict(os.environ, {"SUBMISSION_CONTRACT": "0"}):
            out = self._run(_tool_state("The order is 170."))
        self.assertTrue(out.get("done"))
        self.assertEqual(out.get("answer"), "The order is 170.")


class TestPromptContexts(unittest.TestCase):
    def test_schema_contract_replaces_text_marker_heuristic(self):
        with patch.dict(os.environ, {"SUBMISSION_CONTRACT": "1"}):
            context = nodes._output_format_context(
                "Get the order number of my most recent cancelled order",
                ARRAY_STRINGS, "RETRIEVE",
            )
        self.assertIn("JSON array", context)
        self.assertIn("stop(answer)", context)

    def test_baseline_has_no_schema_context_when_flag_off(self):
        with patch.dict(os.environ, {"SUBMISSION_CONTRACT": "0"}):
            context = nodes._output_format_context(
                "Get the order number of my most recent cancelled order",
                ARRAY_STRINGS, "RETRIEVE",
            )
        self.assertEqual(context, "")

    def test_retrieval_repair_covers_plural_id_tasks(self):
        with patch.dict(os.environ, {"RETRIEVAL_REPAIR": "1"}):
            context = nodes._retrieval_completeness_context(
                "Get the project ID(s) of my personal project(s) that received the most stars",
                ARRAY_STRINGS,
            )
        self.assertIn("pagination", context.casefold())
        self.assertIn("JSON array", context)

    def test_retrieval_repair_is_off_by_default(self):
        with patch.dict(os.environ, {"RETRIEVAL_REPAIR": "0"}):
            context = nodes._retrieval_completeness_context(
                "Get the project ID(s) of my personal project(s)", ARRAY_STRINGS)
        self.assertEqual(context, "")

    def test_both_architectures_receive_the_same_contract(self):
        with patch.dict(os.environ, {"SUBMISSION_CONTRACT": "1"}):
            react = nodes._react_system_prompt(
                "Get the order number", "http://example.test", "[1] text",
                submission_schema=ARRAY_STRINGS,
                submission_task_type="RETRIEVE",
            )
            executor = nodes._plan_executor_prompt(
                "Get the order number", {"goal": "submit"}, "http://example.test",
                "[1] text", submission_schema=ARRAY_STRINGS,
                submission_task_type="RETRIEVE",
            )
        self.assertIn("JSON array", react)
        self.assertIn("JSON array", executor)


class TestStorageStateResolution(unittest.TestCase):
    def test_missing_directory_means_no_state(self):
        self.assertIsNone(get_storage_state_path("gitlab", None))

    def test_existing_and_missing_state_files(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                get_storage_state_path("gitlab", directory)
            path = Path(directory) / "gitlab.json"
            path.write_text("{}", encoding="utf-8")
            self.assertEqual(get_storage_state_path("gitlab", directory), str(path))


class TestContractContext(unittest.TestCase):
    def test_object_schema_lists_keys(self):
        context = contract_context(ARRAY_OBJECTS, "RETRIEVE")
        for key in ("names", "min", "max"):
            self.assertIn(key, context)

    def test_null_schema_explains_no_result(self):
        self.assertIn("NO matching result", contract_context(NULL_SCHEMA, "RETRIEVE"))


if __name__ == "__main__":
    unittest.main()
