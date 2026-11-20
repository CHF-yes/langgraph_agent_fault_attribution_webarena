"""统一的 Agent 任务结果评估逻辑。"""


MAX_STEPS_ANSWER_MARKER = "reached max steps"


def is_cap_exhausted(done: bool, answer: str) -> bool:
    """步数耗尽判定（单一来源）。

    达到 max_steps 时 harness 会合成一个答案串（``nodes.py:_force_stop``），
    因此"是否跑满"只能从 harness 自己的标记判断——判定逻辑只放在这里，
    适配器、trial 记录与分析都复用它，避免各自做字符串匹配。
    """
    return bool(done) and MAX_STEPS_ANSWER_MARKER in str(answer).casefold()


def is_completed(done: bool, answer: str) -> bool:
    """判断 Agent 是否正常结束，而不是因达到最大步数结束。"""
    return bool(done) and not is_cap_exhausted(done, answer)


PROTOCOL_FAILURES = {"no_tool_call", "submission_contract_failed"}


def termination_status(done: bool, answer: str, error: str | None,
                       task_type: str | None = None) -> str:
    """Classify how a trial ended, independent of the official score.

    The categories are deliberately distinct so a record can tell an empty
    submission from an unfinished run, a protocol failure and a real stop.
    """
    if error in PROTOCOL_FAILURES:
        return str(error)
    if is_cap_exhausted(done, answer):
        return "cap_exhausted"
    if not done:
        return "incomplete"
    if (str(task_type or "").upper() == "RETRIEVE"
            and not str(answer or "").strip()):
        return "empty_submission"
    return "stop"


def is_protocol_failure(error: str | None) -> bool:
    return error in PROTOCOL_FAILURES


def terminal_diagnostic(termination: str | None, steps: int | None = None) -> str | None:
    """Human-readable diagnostic for a non-answer terminal state.

    This is what separates a protocol failure or an empty submission from a
    legitimate stop in the official response artifact.
    """
    if termination == "cap_exhausted":
        return f"max_steps exhausted after {steps} steps; no answer produced"
    if termination == "no_tool_call":
        return ("protocol failure: no tool call after the one allowed retry; "
                "no answer submitted")
    if termination == "submission_contract_failed":
        return ("submission protocol failure: corrected stop(answer) was still "
                "invalid after the allowed retry")
    if termination == "empty_submission":
        return "empty submission: stop(answer) contained no answer"
    if termination == "incomplete":
        return "incomplete run: the agent ended without stop(answer)"
    return None


def answer_matches(answer: str, expected: list) -> bool:
    """使用与 Baseline/Benchmark 一致的大小写不敏感子串匹配。"""
    normalized_answer = str(answer or "").casefold()
    return any(
        str(value).casefold() in normalized_answer
        for value in expected
        if value is not None and str(value).strip()
    )


def evaluate_answer(done: bool, answer: str, expected: list) -> tuple[bool, bool]:
    """返回 (completed, success)，统一两类运行器的成功判定。"""
    completed = is_completed(done, answer)
    # 没有可比较的 expected 时，保留操作型任务的 completion 语义；
    # 有 expected 时才把答案匹配作为正确性的必要条件。
    expected_values = [
        value for value in expected
        if value is not None and str(value).strip()
    ]
    return completed, completed and (
        answer_matches(answer, expected_values) if expected_values else True
    )
