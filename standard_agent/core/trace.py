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
    """返回 trace 文件路径：{TRACE_DIR}/{thread_id}.jsonl"""
    trace_dir = os.getenv("TRACE_DIR", "traces")
    return Path(trace_dir) / f"{thread_id}.jsonl"


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
