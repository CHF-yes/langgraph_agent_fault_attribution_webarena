"""Self-checks for standard_agent.core.plan_decision.

Run either way:

    python3 tests/test_plan_decision.py     # plain stdlib, no pytest needed
    python3 -m pytest tests/test_plan_decision.py

The module under test is pure stdlib, so these run on a laptop without
langgraph/langchain installed.
"""

import importlib.util
from pathlib import Path

# Loaded by file path rather than `import standard_agent.core.plan_decision`,
# because the package __init__ pulls in core.graph -> langgraph. The module
# under test is pure stdlib, so the checks should run on a machine that has
# no langgraph installed (e.g. the laptop used to write the patch).
_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "standard_agent" / "core" / "plan_decision.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("plan_decision_under_test", _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_pd = _load_module()
ALIASES = _pd.ALIASES
CANONICAL_DECISIONS = _pd.CANONICAL_DECISIONS
DEFAULT_DECISION = _pd.DEFAULT_DECISION
normalize_decision = _pd.normalize_decision


# --------------------------------------------------------------------------
# canonical inputs -- routing must not change for a well-behaved model
# --------------------------------------------------------------------------

def test_canonical_decisions_are_exact_and_unflagged():
    for token in CANONICAL_DECISIONS:
        result = normalize_decision('{"decision":"%s","reason":"because"}' % token)
        assert result.decision == token, result
        assert result.matched_by == "exact", result
        assert result.contract_violation is False, result
        assert result.json_found is True, result
        assert result.raw_value == token, result


def test_case_and_whitespace_do_not_change_routing():
    result = normalize_decision('{"decision":"  Finish  "}')
    assert result.decision == "finish", result
    assert result.matched_by == "exact", result
    assert result.contract_violation is False, result


def test_json_wrapped_in_prose_or_fences_still_parses():
    raw = 'Sure, here you go:\n```json\n{"decision": "finish"}\n```\nHope that helps.'
    result = normalize_decision(raw)
    assert result.decision == "finish", result
    assert result.matched_by == "exact", result


# --------------------------------------------------------------------------
# the regression this module exists for
# --------------------------------------------------------------------------

def test_synonym_for_continue_no_longer_becomes_a_replan():
    """The load-bearing case: `next` used to fall through to a full replan."""
    result = normalize_decision('{"decision":"next"}')
    assert result.decision == "continue", result
    assert result.decision != "replan", result
    assert result.matched_by == "alias", result
    assert result.contract_violation is True, result
    assert result.raw_value == "next", result


def test_every_alias_maps_and_is_flagged():
    for alias, expected in sorted(ALIASES.items()):
        for surface in (alias, alias.upper(), alias.replace("_", "-"), alias.replace("_", " ")):
            result = normalize_decision('{"decision":"%s"}' % surface)
            assert result.decision == expected, (surface, result)
            assert result.matched_by == "alias", (surface, result)
            assert result.contract_violation is True, (surface, result)


def test_alias_targets_are_canonical():
    for alias, target in ALIASES.items():
        assert target in CANONICAL_DECISIONS, (alias, target)


# --------------------------------------------------------------------------
# degenerate inputs -- must never raise, and must stay countable
# --------------------------------------------------------------------------

def test_missing_decision_key_defaults_and_is_flagged():
    result = normalize_decision('{"reason":"the page did not load"}')
    assert result.decision == DEFAULT_DECISION, result
    assert result.matched_by == "default", result
    assert result.contract_violation is True, result
    # JSON parsed fine -- the key was simply absent. Distinct from malformed.
    assert result.json_found is True, result
    assert result.raw_value == "", result


def test_malformed_output_defaults_without_json():
    for raw in ("I think we should continue", "", "   ", "{not json}", None):
        result = normalize_decision(raw)
        assert result.decision == DEFAULT_DECISION, (raw, result)
        assert result.matched_by == "default", (raw, result)
        assert result.contract_violation is True, (raw, result)
        assert result.json_found is False, (raw, result)


def test_non_object_json_is_not_treated_as_a_decision():
    for raw in ('["continue"]', '"finish"', "42"):
        result = normalize_decision(raw)
        assert result.decision == DEFAULT_DECISION, (raw, result)
        assert result.json_found is False, (raw, result)


def test_non_string_decision_value_does_not_crash():
    result = normalize_decision('{"decision": true}')
    assert result.decision == DEFAULT_DECISION, result
    assert result.matched_by == "default", result
    assert result.raw_value == "true", result


def test_unknown_token_defaults_but_keeps_the_raw_value():
    result = normalize_decision('{"decision":"escalate"}')
    assert result.decision == DEFAULT_DECISION, result
    assert result.matched_by == "default", result
    assert result.raw_value == "escalate", result
    assert result.contract_violation is True, result


# --------------------------------------------------------------------------
# extractor hardening (old greedy regex could be poisoned by trailing braces)
# --------------------------------------------------------------------------

def test_trailing_second_object_does_not_poison_the_first():
    raw = '{"decision":"next"} and then {"decision":"replan"}'
    result = normalize_decision(raw)
    assert result.decision == "continue", result
    assert result.matched_by == "alias", result


def test_brace_in_prose_is_skipped():
    raw = 'Set {a} then answer: {"decision":"replan"}'
    result = normalize_decision(raw)
    assert result.decision == "replan", result
    assert result.matched_by == "exact", result


def test_nested_object_still_parses():
    raw = '{"decision":"continue","meta":{"conf":0.9}}'
    result = normalize_decision(raw)
    assert result.decision == "continue", result
    assert result.matched_by == "exact", result


# --------------------------------------------------------------------------
# never-raises sweep over adversarial strings
# --------------------------------------------------------------------------

def test_normalize_never_raises():
    probes = [
        '{"decision":', '{"decision": null}', '{{}}', '}{', '{"decision": ["a"]}',
        '{"decision": {"x": 1}}', '\x00{"decision":"finish"}', '{"decision":"CONTINUE"}',
        '{"decision":"  re-plan  "}', '{"decision":"keep going"}',
    ]
    for raw in probes:
        result = normalize_decision(raw)
        assert result.decision in CANONICAL_DECISIONS, (raw, result)
        assert result.matched_by in ("exact", "alias", "default"), (raw, result)


# --------------------------------------------------------------------------

def _main() -> int:
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failures = []
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failures.append((name, exc))
            print("FAIL %s: %s" % (name, exc))
        else:
            print("ok   %s" % name)
    print("\n%d/%d passed" % (len(tests) - len(failures), len(tests)))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
