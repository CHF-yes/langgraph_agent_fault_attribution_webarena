"""
TraceRecorder — 将 agent 每一步的 ReAct 轨迹持久化为 JSONL。

每条记录包含：时间、会话、步骤、任务、URL、thought、action、args、
observation、error 标记。这是鲁棒性归因分析的基础数据。

输出目录可用环境变量 TRACE_DIR 覆盖，默认 ./traces。
"""

import json
import os
import time
from pathlib import Path


def get_trace_path(thread_id: str) -> Path:
    """返回 trace 文件路径：{TRACE_DIR}/{TRACE_RUN_ID}/{thread_id}.jsonl

    ``TRACE_RUN_ID`` 让每次 trial 运行落在自己的目录里，因此**同一格重跑不会把
    新记录追加到旧 trace 上**。未设置时退回旧的扁平布局（保证向后兼容）。
    """
    trace_dir = Path(os.getenv("TRACE_DIR", "traces"))
    run_id = os.getenv("TRACE_RUN_ID", "").strip()
    if run_id:
        return trace_dir / run_id / f"{thread_id}.jsonl"
    return trace_dir / f"{thread_id}.jsonl"


def prepare_trace(thread_id: str) -> Path:
    """在本次 trial 第一次写 trace 之前调用，保证不会追加到旧文件。

    即便 ``TRACE_RUN_ID`` 没变（例如手工重跑同一 run id），只要目标文件已存在且
    非空，就把它重命名成 ``<name>.<ts>.prev`` 而不是续写。重命名失败不阻断任务：
    trace 是诊断产物，不能因为它让 trial 失败。
    """
    path = get_trace_path(thread_id)
    try:
        if path.exists() and path.stat().st_size > 0:
            rotated = path.with_name(f"{path.name}.{int(time.time())}.prev")
            os.replace(path, rotated)
    except OSError:
        pass
    return path


def append_trace(thread_id: str, entry: dict) -> None:
    """
    追加一条 trace 记录。写入失败不抛异常（trace 不应中断任务）。
    """
    if not thread_id:
        return
    try:
        path = get_trace_path(thread_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def make_entry(thread_id: str, *, step: int, task: str, url: str,
               thought: str, action: str, args: dict,
               observation: str, error: bool, done: bool = False,
               answer: str = "", extra: dict = None) -> dict:
    """构造一条标准 trace 记录。"""
    entry = {
        "timestamp": time.time(),
        "thread_id": thread_id,
        "step": step,
        "task": task,
        "url": url,
        "thought": thought,
        "action": action,
        "args": args,
        "observation": observation[:2000],  # observation 截断，避免文件过大
        "error": error,
        "done": done,
        "answer": answer,
    }
    if extra:
        entry.update(extra)
    return entry


def append_event(thread_id: str, *, event: str, step: int, task: str,
                 url: str, data: dict) -> None:
    """Append a non-action lifecycle event to the same attribution trace."""
    append_trace(thread_id, make_entry(
        thread_id,
        step=step,
        task=task,
        url=url,
        thought="",
        action="",
        args={},
        observation="",
        error=False,
        extra={"event": event, **data},
    ))
