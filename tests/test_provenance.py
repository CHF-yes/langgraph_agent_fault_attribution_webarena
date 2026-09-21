#!/usr/bin/env python3
"""Checks for ``extract_served_metadata`` -- the guard against silent model substitution.

Why this file exists: the paper's model comparison is only meaningful if the two
arms actually ran on two different models.  ``get_llm_metadata`` records the id we
*requested*, which is exactly the quantity a provider can lie about -- by reselling
a smaller model under a flagship name, or by aliasing ids across backends the way
DeepSeek did during its 2026-09 V4 Pro flip-flop.  ``extract_served_metadata`` reads
what the endpoint *says it served* instead.  If it silently returns empty strings,
every downstream check passes while detecting nothing, so its behaviour is pinned
here rather than left to the trial run to reveal.

Run either way::

    python3 -m pytest tests/test_provenance.py
    python3 tests/test_provenance.py

No third-party dependency is required; ``pytest`` is used only if present.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_provider():
    """Load ``provider.py`` on a machine without langchain or langgraph.

    The module imports ``langchain_openai`` at scope, and importing the
    ``standard_agent`` package runs an ``__init__`` that pulls in langgraph --
    neither is installed where the statistics and the paper pipeline run.
    Stubbing just those two names lets the extractor (which needs neither) be
    exercised anywhere, including CI that never touches the agent runtime.
    """
    if "langchain_openai" not in sys.modules:
        stub = types.ModuleType("langchain_openai")
        stub.ChatOpenAI = object  # only referenced as a return annotation
        sys.modules["langchain_openai"] = stub
    if "standard_agent.config" not in sys.modules:
        pkg = types.ModuleType("standard_agent")
        pkg.__path__ = []
        cfg = types.ModuleType("standard_agent.config")
        cfg.ModelProfile = type("ModelProfile", (), {})
        cfg.settings = types.SimpleNamespace()
        sys.modules["standard_agent"] = pkg
        sys.modules["standard_agent.config"] = cfg
    spec = importlib.util.spec_from_file_location(
        "provider_under_test",
        os.path.join(ROOT, "standard_agent", "llm", "provider.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


extract = _load_provider().extract_served_metadata


class _Resp:
    """A stand-in for a LangChain AIMessage -- only the two attributes matter."""

    def __init__(self, response_metadata=None, usage_metadata=None):
        if response_metadata is not None:
            self.response_metadata = response_metadata
        if usage_metadata is not None:
            self.usage_metadata = usage_metadata


# --------------------------------------------------------------------------
# The substitution signal itself
# --------------------------------------------------------------------------
def test_served_model_is_read_from_the_response():
    got = extract(
        _Resp({"model": "deepseek-v4-pro", "system_fingerprint": "fp_abc"}),
        requested_model="deepseek-v4-pro",
    )
    assert got["served_model"] == "deepseek-v4-pro"
    assert got["system_fingerprint"] == "fp_abc"
    assert got["served_model_differs"] is False


def test_substitution_is_flagged():
    """The case the whole mechanism exists for: we asked for the flagship and a
    different model answered."""
    got = extract(_Resp({"model": "deepseek-flash"}), requested_model="deepseek-v4-pro")
    assert got["served_model"] == "deepseek-flash"
    assert got["served_model_differs"] is True


def test_dated_variant_trips_the_flag_and_that_is_expected():
    """A false alarm by design.  Providers legitimately answer with a dated build
    of the requested id, so the flag means "go look", not "substituted".  Pinning
    it here stops someone from later "fixing" the extractor into a false negative.
    """
    got = extract(_Resp({"model": "deepseek-v4-pro-0813"}), "deepseek-v4-pro")
    assert got["served_model_differs"] is True


def test_gateway_model_name_key_is_accepted():
    got = extract(_Resp({"model_name": "gpt-4o-2024-11-20"}), "gpt-4o")
    assert got["served_model"] == "gpt-4o-2024-11-20"


# --------------------------------------------------------------------------
# Never raise: a provider that echoes nothing must not break a trial
# --------------------------------------------------------------------------
def test_missing_metadata_degrades_to_unknown_not_match():
    """An absent served id means *unknown*.  Reporting it as a match would turn
    "we could not check" into "we checked and it was fine" -- the exact
    inversion this test exists to prevent."""
    got = extract(_Resp(), requested_model="deepseek-v4-pro")
    assert got["served_model"] == ""
    assert got["system_fingerprint"] == ""
    assert got["served_model_differs"] is False


def test_malformed_metadata_does_not_raise():
    for bad in (None, "not-a-dict", 42, [], {"token_usage": "nope"}):
        got = extract(_Resp(bad), "m")
        assert got["served_model"] == ""
        assert got["served_model_differs"] is False


def test_empty_requested_model_never_flags():
    # Without a requested id there is nothing to compare against.
    assert extract(_Resp({"model": "whatever"}), "")["served_model_differs"] is False


def test_blank_served_model_is_not_treated_as_a_difference():
    got = extract(_Resp({"model": "   "}), "deepseek-v4-pro")
    assert got["served_model"] == ""
    assert got["served_model_differs"] is False


# --------------------------------------------------------------------------
# Token accounting: two different shapes in the wild
# --------------------------------------------------------------------------
def test_tokens_from_openai_style_token_usage():
    got = extract(_Resp({"model": "m", "token_usage": {
        "prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}))
    assert (got["prompt_tokens"], got["completion_tokens"], got["total_tokens"]) == (11, 7, 18)


def test_tokens_fall_back_to_usage_metadata():
    got = extract(_Resp({"model": "m"}, {"input_tokens": 5, "output_tokens": 3}))
    assert (got["prompt_tokens"], got["completion_tokens"]) == (5, 3)


def test_absent_tokens_are_none_not_zero():
    """None means "not reported"; 0 would read as "reported zero", which is a
    different claim and would quietly corrupt any cost column."""
    got = extract(_Resp({"model": "m"}))
    assert got["prompt_tokens"] is None
    assert got["completion_tokens"] is None


# --------------------------------------------------------------------------
# Prompt-cache accounting: an unreported field is "unknown", never zero
# --------------------------------------------------------------------------
def test_cache_hit_and_miss_from_deepseek_shape():
    got = extract(_Resp({"model": "m", "token_usage": {
        "prompt_tokens": 6095, "prompt_cache_hit_tokens": 6016,
        "prompt_cache_miss_tokens": 79}}))
    assert got["prompt_cache_hit_tokens"] == 6016
    assert got["prompt_cache_miss_tokens"] == 79
    assert got["cache_usage_source"] == "deepseek"


def test_cache_read_from_openai_prompt_tokens_details():
    """OpenAI reports only the cached read, so the miss side is derived."""
    got = extract(_Resp({"model": "m", "token_usage": {
        "prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 768}}}))
    assert got["prompt_cache_hit_tokens"] == 768
    assert got["prompt_cache_miss_tokens"] == 232
    assert got["cache_usage_source"] == "openai"


def test_cache_read_from_langchain_usage_metadata():
    got = extract(_Resp(
        {"model": "m"},
        {"input_tokens": 500, "output_tokens": 5,
         "input_token_details": {"cache_read": 400}},
    ))
    assert got["prompt_cache_hit_tokens"] == 400
    assert got["prompt_cache_miss_tokens"] == 100
    assert got["cache_usage_source"] == "langchain"


def test_absent_cache_fields_are_none_not_zero():
    """0 would mean "the cache returned nothing", which is a measurement.  A
    missing field means the endpoint did not report it, so the cache share for
    the call is unknown and must not be summed as a miss."""
    got = extract(_Resp({"model": "m", "token_usage": {"prompt_tokens": 42}}))
    assert got["prompt_cache_hit_tokens"] is None
    assert got["prompt_cache_miss_tokens"] is None
    assert got["cache_usage_source"] == ""


def test_result_is_json_serialisable():
    """The dict is merged into a trace event and written as JSONL."""
    got = extract(_Resp({"model": "m", "token_usage": {"total_tokens": 3}}), "m")
    assert json.loads(json.dumps(got)) == got


# --------------------------------------------------------------------------
if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
    print(f"\n{'OK' if not failures else str(failures) + ' FAILED'}")
    sys.exit(1 if failures else 0)
