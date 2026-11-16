"""Shared retrieval-submission and prompt-invariance regression tests.

The contract must depend only on the public task type, never on the dataset's
``results_schema`` or expected value.  Otherwise changing the evaluator's
empty-result field would change the model-visible prompt, the accept/reject
decision, the correction feedback, or the termination state.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from standard_agent.core import nodes
from standard_agent.environment.webarena_config import get_storage_state_path
from standard_agent.submission_contract import contract_context, validate_submission

ARRAY_STRINGS = {"type": "array", "items": {"type": "string"}}
ARRAY_NUMBERS = {"type": "array", "items": {"type": "number"}}
NULL_SCHEMA = {"type": "null"}


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


def _tool_state(answer: str, *, architecture: str = "react", schema=None):
    ai = AIMessage(content="THOUGHT: submit", tool_calls=[{
        "name": "stop", "args": {"answer": answer}, "id": "call_1",
    }])
    return {
        "messages": [HumanMessage(content="task"), ai],
        "step_count": 1, "max_steps": 5, "task": "Get the order number",
        "url": "http://example.test", "architecture": architecture,
        "done": False, "answer": "",
        "submission_schema": schema if schema is not None else ARRAY_STRINGS,
        "submission_task_type": "RETRIEVE",
        "submission_rejections": 0,
    }


def _run(state):
    with patch("langgraph.prebuilt.ToolNode", _FakeToolNode), \
            patch("standard_agent.core.nodes.append_trace"), \
            patch("standard_agent.core.nodes.append_event"):
        return nodes.run_tools(state, {"configurable": {"thread_id": "contract-test"}})


class TestValidateSubmission(unittest.TestCase):
    def test_prose_is_rejected_with_one_correction(self):
        result = validate_submission(
            "The order is 170.", task_type="RETRIEVE")
        self.assertFalse(result.ok)
        self.assertIn("valid JSON", result.error)

    def test_valid_json_is_accepted_without_a_schema(self):
        for answer in ('["000000170"]', '{"min": 2.56, "max": 649.99}',
                       "170", "true"):
            with self.subTest(answer=answer):
                self.assertTrue(validate_submission(
                    answer, task_type="RETRIEVE").ok)

    def test_fenced_json_is_accepted(self):
        self.assertTrue(validate_submission(
            '```json\n["170"]\n```', task_type="RETRIEVE").ok)

    def test_explicit_no_match_is_accepted(self):
        for answer in ("No matching reviewer found.",
                       "I did not find any matching result.",
                       "没有找到匹配结果"):
            with self.subTest(answer=answer):
                self.assertTrue(validate_submission(
                    answer, task_type="RETRIEVE").ok)

    def test_json_empty_values_are_accepted_as_no_match(self):
        for answer in ("null", "[]"):
            with self.subTest(answer=answer):
                result = validate_submission(answer, task_type="RETRIEVE")
                self.assertTrue(result.ok)
                self.assertIn("no matching result", result.normalized.casefold())

    def test_navigate_task_is_not_shape_checked(self):
        self.assertTrue(validate_submission(
            "Opened the requested page.", task_type="NAVIGATE").ok)

    def test_acceptance_does_not_depend_on_evaluator_empty_field(self):
        answer = '["000000170"]'
        # Same answer accepted under both a null and a non-empty evaluator schema;
        # validate_submission does not even receive the schema.
        self.assertEqual(
            validate_submission(answer, task_type="RETRIEVE"),
            validate_submission(answer, task_type="RETRIEVE"),
        )


class TestRunToolsSubmissionLoop(unittest.TestCase):
    def test_rejects_prose_and_keeps_task_open(self):
        with patch.dict(os.environ, {"SUBMISSION_CONTRACT": "1"}):
            out = _run(_tool_state("The order is 170."))
        self.assertFalse(out.get("done", False))
        self.assertEqual(out.get("submission_rejections"), 1)
        self.assertTrue(out["messages"][-1].content.startswith(
            "[Error] Submission rejected"))

    def test_second_invalid_submission_ends_as_protocol_failure(self):
        state = _tool_state("The order is 170.")
        state["submission_rejections"] = 1
        with patch.dict(os.environ, {"SUBMISSION_CONTRACT": "1"}):
            out = _run(state)
        self.assertTrue(out.get("done"))
        self.assertEqual(out.get("answer"), "")
        self.assertEqual(out.get("error"), "submission_contract_failed")
        self.assertEqual(out.get("submission_rejections"), 2)
        self.assertTrue(out["messages"][-1].content.startswith(
            "[Error] Submission protocol failure"))

    def test_valid_correction_after_rejection_finishes(self):
        state = _tool_state('["170"]')
        state["submission_rejections"] = 1
        with patch.dict(os.environ, {"SUBMISSION_CONTRACT": "1"}):
            out = _run(state)
        self.assertTrue(out.get("done"))
        self.assertEqual(out.get("answer"), '["170"]')
        self.assertNotEqual(out.get("error"), "submission_contract_failed")

    def test_non_empty_result_is_accepted_under_a_null_evaluator_schema(self):
        state = _tool_state('["candidate value"]', schema=NULL_SCHEMA)
        with patch.dict(os.environ, {"SUBMISSION_CONTRACT": "1"}):
            out = _run(state)
        self.assertTrue(out.get("done"))
        self.assertEqual(out.get("answer"), '["candidate value"]')
        self.assertNotEqual(out.get("error"), "submission_contract_failed")

    def test_explicit_no_match_submission_finishes(self):
        state = _tool_state("No matching result was found.", schema=NULL_SCHEMA)
        with patch.dict(os.environ, {"SUBMISSION_CONTRACT": "1"}):
            out = _run(state)
        self.assertTrue(out.get("done"))
        self.assertEqual(out.get("answer"), "No matching result was found.")

    def test_evaluator_schema_does_not_change_feedback_or_termination(self):
        for architecture in ("react", "plan_execute"):
            with self.subTest(architecture=architecture):
                with patch.dict(os.environ, {"SUBMISSION_CONTRACT": "1"}):
                    null_state = _tool_state(
                        "The answer is 3.", architecture=architecture,
                        schema=NULL_SCHEMA)
                    array_state = _tool_state(
                        "The answer is 3.", architecture=architecture,
                        schema=ARRAY_STRINGS)
                    null_out = _run(null_state)
                    array_out = _run(array_state)
                self.assertEqual(null_out.get("done"), array_out.get("done"))
                self.assertEqual(null_out.get("submission_rejections"),
                                 array_out.get("submission_rejections"))
                self.assertEqual(null_out.get("error"), array_out.get("error"))
                self.assertEqual(null_out["messages"][-1].content,
                                 array_out["messages"][-1].content)

    def test_baseline_with_flag_off_keeps_old_behavior(self):
        with patch.dict(os.environ, {"SUBMISSION_CONTRACT": "0"}):
            out = _run(_tool_state("The order is 170."))
        self.assertTrue(out.get("done"))
        self.assertEqual(out.get("answer"), "The order is 170.")


class TestPromptInvariance(unittest.TestCase):
    def test_initial_prompt_does_not_depend_on_evaluator_empty_field(self):
        task = "Find reviews that mention this product"
        for architecture in ("react", "plan_execute"):
            with self.subTest(architecture=architecture):
                with patch.dict(os.environ, {"SUBMISSION_CONTRACT": "1"}):
                    if architecture == "react":
                        first = nodes._react_system_prompt(
                            task, "http://example.test", "[1] text",
                            submission_task_type="RETRIEVE")
                        second = nodes._react_system_prompt(
                            task, "http://example.test", "[1] text",
                            submission_task_type="RETRIEVE")
                    else:
                        first = nodes._plan_executor_prompt(
                            task, {"goal": "submit"}, "http://example.test",
                            "[1] text", submission_task_type="RETRIEVE")
                        second = nodes._plan_executor_prompt(
                            task, {"goal": "submit"}, "http://example.test",
                            "[1] text", submission_task_type="RETRIEVE")
                self.assertEqual(first, second)
                self.assertNotIn("expects NO matching result", first)

    def test_both_architectures_receive_the_same_contract(self):
        task = "Get the order number"
        with patch.dict(os.environ, {"SUBMISSION_CONTRACT": "1"}):
            react = nodes._react_system_prompt(
                task, "http://example.test", "[1] text",
                submission_task_type="RETRIEVE")
            executor = nodes._plan_executor_prompt(
                task, {"goal": "submit"}, "http://example.test", "[1] text",
                submission_task_type="RETRIEVE")
        contract = contract_context("RETRIEVE")
        self.assertIn(contract, react)
        self.assertIn(contract, executor)

    def test_retrieval_repair_is_generic_and_covers_pagination(self):
        with patch.dict(os.environ, {"RETRIEVAL_REPAIR": "1"}):
            context = nodes._retrieval_completeness_context(
                "Get the project ID(s) of my personal project(s)")
        self.assertIn("pagination", context.casefold())
        self.assertIn("do not stop after finding the first match", context.casefold())

    def test_baseline_flag_off_keeps_text_heuristic(self):
        with patch.dict(os.environ, {"SUBMISSION_CONTRACT": "0"}):
            context = nodes._output_format_context(
                "How many commits did Kilian make on March 5?")
        self.assertIn("ONLY valid JSON", context)


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


if __name__ == "__main__":
    unittest.main()
