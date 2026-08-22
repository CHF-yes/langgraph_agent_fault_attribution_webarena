"""统一的 Agent 任务结果评估逻辑。"""


def is_completed(done: bool, answer: str) -> bool:
    """判断 Agent 是否正常结束，而不是因达到最大步数结束。"""
    return bool(done) and "reached max steps" not in str(answer).casefold()


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
