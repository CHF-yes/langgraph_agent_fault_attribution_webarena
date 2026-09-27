#!/usr/bin/env python3
"""端点级延迟探针：空闲后首次调用 vs 连续调用，以及固定间隔 ping 的 A/B。

**不启动 Agent trial**，只发最小补全（max_tokens 很小）。回答两个问题：

1. 闲置之后第一次调用是否明显变慢（网关空闲驱逐）？
2. 在同样的闲置窗口里，按固定间隔 ping 是否能消除这种慢调用？
   —— 用"停掉 ping 再闲置一次"作为负对照，避免把偶发波动当成 ping 的功劳。

用法::

    python scripts/probe_endpoint_idle_latency.py \
        --profiles qwen38_flash deepseek_v41_flash \
        --out experiments/endpoint_probe/idle_latency_probe.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SLOW_THRESHOLD_S = 30.0            # 明显高于稳态（1–3s）即可判定为"慢"


def _call(llm, label: str, counter: list) -> dict:
    """发一次最小补全，记录延迟与 token。"""
    counter[0] += 1
    prompt = f"Reply with exactly: OK ({counter[0]})"
    started = time.time()
    record = {"label": label, "prompt_chars": len(prompt), "started_at": started}
    try:
        response = llm.invoke(prompt, config={"max_tokens": 8})
        record["content"] = str(response.content)[:24]
        meta = getattr(response, "response_metadata", {}) or {}
        usage = getattr(response, "usage_metadata", None) or {}
        record["prompt_tokens"] = usage.get("input_tokens") if isinstance(usage, dict) else None
        record["completion_tokens"] = usage.get("output_tokens") if isinstance(usage, dict) else None
        record["served_model"] = meta.get("model_name") or meta.get("model")
        record["ok"] = True
    except Exception as exc:  # noqa: BLE001 - 探针要记录失败而不是中断
        record["ok"] = False
        record["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    record["latency_s"] = round(time.time() - started, 3)
    record["slow"] = record["latency_s"] > SLOW_THRESHOLD_S
    print(f"    {label:34} {record['latency_s']:8.1f}s slow={record['slow']} "
          f"ok={record['ok']} served={record.get('served_model')}", flush=True)
    return record


def probe_profile(profile: str, args, flushes: list, on_call=None) -> dict:
    from standard_agent.config import settings
    from standard_agent.llm.provider import create_llm

    config = settings.get_model_profile(profile)
    llm = create_llm(temperature=0.0, profile_name=profile)
    counter = [0]
    calls: list[dict] = []

    def call(label: str) -> dict:
        record = _call(llm, label, counter)
        calls.append(record)
        flushes.append({"profile": profile, **record})
        if on_call is not None:
            on_call()
        return record

    print(f"\n=== {profile} (model={config.model}, timeout={config.request_timeout:.0f}s)",
          flush=True)

    # 1) 暖机 + 连续调用基线
    call("warmup")
    for index in range(1, args.consecutive + 1):
        call(f"consecutive_{index}")

    # 2) 闲置后第一次调用
    print(f"    idle {args.idle_seconds}s …", flush=True)
    time.sleep(args.idle_seconds)
    idle_first = call("after_idle_first")
    for index in range(1, args.consecutive + 1):
        call(f"after_idle_consecutive_{index}")

    # 3) 间隔 ping 期间：在 ping 的同时睡满一个闲置窗口，再发"真实"调用
    print(f"    keepalive ping every {args.ping_interval}s for {args.ping_duration}s …",
          flush=True)
    stop = threading.Event()
    pings: list[dict] = []

    def pinger() -> None:
        while not stop.wait(args.ping_interval):
            pings.append(_call(llm, "keepalive_ping", counter))

    thread = threading.Thread(target=pinger, daemon=True)
    thread.start()
    time.sleep(args.ping_duration)
    call("during_keepalive_real")
    stop.set()
    thread.join(timeout=30)
    flushes.extend({"profile": profile, **ping} for ping in pings)

    # 4) 负对照：停掉 ping，再闲置同样时长，慢调用应复发
    print(f"    ping stopped, idle {args.idle_seconds}s again …", flush=True)
    time.sleep(args.idle_seconds)
    control = call("after_ping_stopped_idle")

    steady = [c["latency_s"] for c in calls
              if c["label"].startswith("consecutive") or c["label"].startswith("after_idle_consecutive")
              or c["label"] == "during_keepalive_real"]
    return {
        "profile": profile,
        "model": config.model,
        "base_url": config.base_url,
        "request_timeout_s": config.request_timeout,
        "calls": calls,
        "keepalive_pings": pings,
        "summary": {
            "steady_median_s": statistics.median(steady) if steady else None,
            "after_idle_first_s": idle_first["latency_s"],
            "after_idle_first_slow": idle_first["slow"],
            "during_keepalive_slow": [c["slow"] for c in calls
                                      if c["label"] == "during_keepalive_real"],
            "after_ping_stopped_slow": control["slow"],
            "ping_count": len(pings),
            "ping_slow_count": sum(1 for ping in pings if ping["slow"]),
        },
    }


def probe_idle_ladder(profile: str, ladder: list[float], args, flushes: list,
                      on_call=None) -> dict:
    """依次测量"闲置 d 秒后首次调用"的延迟，定位驱逐阈值。"""
    from standard_agent.config import settings
    from standard_agent.llm.provider import create_llm

    config = settings.get_model_profile(profile)
    llm = create_llm(temperature=0.0, profile_name=profile)
    counter = [0]
    calls: list[dict] = []

    def call(label: str) -> dict:
        record = _call(llm, label, counter)
        calls.append(record)
        flushes.append({"profile": profile, **record})
        if on_call is not None:
            on_call()
        return record

    print(f"\n=== {profile} idle ladder {ladder} (model={config.model})", flush=True)
    call("warmup")
    call("warm_confirm")
    results = []
    for idle in ladder:
        print(f"    idle {idle}s …", flush=True)
        time.sleep(idle)
        record = call(f"after_idle_{int(idle)}s")
        results.append({"idle_s": idle, "latency_s": record["latency_s"],
                        "slow": record["slow"], "ok": record["ok"]})
    return {"profile": profile, "mode": "idle_ladder", "ladder_results": results,
            "calls": calls,
            "summary": {"threshold_s_estimate": next(
                (item["idle_s"] for item in results if item["slow"]), None),
                "steady_warm_s": calls[1]["latency_s"]}}


def probe_true_concurrency(profile: str, args, flushes: list, on_call=None) -> dict:
    """真并发：N 个请求**同时**发出（调用期间不持锁），各做 M 轮。

    与 probe_concurrency 的区别：那里用锁把调用串行化了，只能证明"串行且间隔短就快"，
    无法回答"并发请求是否会让网关分叉出未预热的实例、从而每个新实例付一次冷启动"。
    这里刻意去掉调用期间的锁，只保留记账用的锁。
    """
    from standard_agent.config import settings
    from standard_agent.llm.provider import create_llm

    config = settings.get_model_profile(profile)
    llm = create_llm(temperature=0.0, profile_name=profile)
    counter = [0]
    booking = threading.Lock()
    calls: list[dict] = []
    barrier = threading.Barrier(args.concurrency)

    def worker(index: int) -> None:
        for round_index in range(args.true_rounds):
            try:
                barrier.wait(timeout=600)          # 让 N 个请求尽量同时发出
            except threading.BrokenBarrierError:
                break
            with booking:
                counter[0] += 1
                seq = counter[0]
            started = time.time()
            prompt = f"Reply with exactly: OK ({seq})"
            record = {"label": f"true_conc_r{round_index}_t{index}", "prompt_chars": len(prompt)}
            try:
                response = llm.invoke(prompt, config={"max_tokens": 8})
                record["content"] = str(response.content)[:24]
                usage = getattr(response, "usage_metadata", None) or {}
                record["prompt_tokens"] = usage.get("input_tokens") if isinstance(usage, dict) else None
                record["completion_tokens"] = usage.get("output_tokens") if isinstance(usage, dict) else None
                record["served_model"] = (getattr(response, "response_metadata", {}) or {}).get("model_name")
                record["ok"] = True
            except Exception as exc:  # noqa: BLE001
                record["ok"] = False
                record["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            record["started_at"] = started
            record["latency_s"] = round(time.time() - started, 3)
            record["slow"] = record["latency_s"] > SLOW_THRESHOLD_S
            with booking:
                calls.append(record)
                flushes.append({"profile": profile, **record})
                if on_call is not None:
                    on_call()
            print(f"    {record['label']:20} {record['latency_s']:8.1f}s slow={record['slow']} "
                  f"ok={record['ok']}", flush=True)

    print(f"\n=== {profile} TRUE concurrency={args.true_concurrency} "
          f"rounds={args.true_rounds} (同时发出，不串行)", flush=True)
    # 预热一次，避免把"首次必然 270s"算进来
    warm = _call(llm, "true_warmup", counter)
    calls.append(warm)
    flushes.append({"profile": profile, **warm})
    if on_call is not None:
        on_call()

    threads = [threading.Thread(target=worker, args=(i,), daemon=False)
               for i in range(args.concurrency)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    measured = [c for c in calls if c["label"] != "true_warmup"]
    latencies = [c["latency_s"] for c in measured]
    slow = [c for c in measured if c["slow"]]
    return {
        "profile": profile, "mode": "true_concurrency", "calls": calls,
        "summary": {
            "concurrency": args.true_concurrency, "rounds": args.true_rounds,
            "warmup_s": warm["latency_s"],
            "calls": len(measured), "slow_calls": len(slow),
            "median_s": statistics.median(latencies) if latencies else None,
            "max_s": max(latencies) if latencies else None,
        },
    }


def probe_concurrency(profile: str, args, flushes: list, on_call=None) -> dict:
    """并发保持保温：N 个线程各按固定间隔调用，看延迟是否稳定在稳态。

    这是检测 T0 排程可行性的关键：若"始终有请求在飞"能让网关不缩容，则提高 worker
    数即可消除慢调用；若并发下仍然出现 ~270s 慢调用，则该端点只能接受冷启动税。
    """
    from standard_agent.config import settings
    from standard_agent.llm.provider import create_llm

    config = settings.get_model_profile(profile)
    llm = create_llm(temperature=0.0, profile_name=profile)
    counter = [0]
    lock = threading.Lock()
    calls: list[dict] = []
    stop = threading.Event()

    def worker(index: int) -> None:
        while not stop.is_set():
            try:
                with lock:
                    record = _call(llm, f"concurrent_{index}", counter)
                    calls.append(record)
                    flushes.append({"profile": profile, **record})
                    if on_call is not None:
                        on_call()
            except Exception as exc:  # noqa: BLE001 - 线程不得因单次失败退出
                print(f"    concurrent_{index} worker error: {type(exc).__name__}: "
                      f"{str(exc)[:160]}", flush=True)
            stop.wait(args.concurrency_interval)

    print(f"\n=== {profile} concurrency={args.concurrency} "
          f"interval={args.concurrency_interval}s duration={args.concurrency_duration}s",
          flush=True)
    # 先预热：把"首次调用必然 270s"这一步与并发保温的测量分开，否则窗口被首调吃掉。
    with lock:
        warmup = _call(llm, "concurrency_warmup", counter)
        calls.append(warmup)
        flushes.append({"profile": profile, **warmup})
        if on_call is not None:
            on_call()
    threads = [threading.Thread(target=worker, args=(i,), daemon=False)
               for i in range(args.concurrency)]
    for thread in threads:
        thread.start()
    time.sleep(args.concurrency_duration)
    stop.set()
    # 显式等待在途调用结束：不能因为 join 超时就把它们丢掉（那会把"慢"误报成"少"）。
    for thread in threads:
        thread.join()
    measured = [c for c in calls if c["label"] != "concurrency_warmup"]

    latencies = [c["latency_s"] for c in measured]
    slow = [c for c in measured if c["slow"]]
    return {
        "profile": profile, "mode": "concurrency", "calls": calls,
        "summary": {
            "concurrency": args.concurrency,
            "interval_s": args.concurrency_interval,
            "duration_s": args.concurrency_duration,
            "calls": len(measured),
            "slow_calls": len(slow),
            "warmup_s": warmup["latency_s"],
            "median_s": statistics.median(latencies) if latencies else None,
            "max_s": max(latencies) if latencies else None,
            "p95_s": (sorted(latencies)[int(0.95 * (len(latencies) - 1))]
                      if latencies else None),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profiles", nargs="+", default=["qwen38_flash"])
    parser.add_argument("--idle-seconds", type=float, default=240)
    parser.add_argument("--consecutive", type=int, default=3)
    parser.add_argument("--ping-interval", type=float, default=120)
    parser.add_argument("--ping-duration", type=float, default=480)
    parser.add_argument("--out", required=True)
    parser.add_argument("--true-concurrency", type=int, default=0,
                        help="真并发模式：N 个请求同时发出（不做串行化），测网关是否分叉出冷实例")
    parser.add_argument("--true-rounds", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=0,
                        help="并发保温模式：N 个线程同时按 --concurrency-interval 调用")
    parser.add_argument("--concurrency-interval", type=float, default=10)
    parser.add_argument("--concurrency-duration", type=float, default=180)
    parser.add_argument("--idle-ladder", type=float, nargs="+", default=None,
                        help="阈值模式：依次测量闲置这些秒数后的首次调用延迟")
    options = parser.parse_args()

    out = ROOT / options.out
    out.parent.mkdir(parents=True, exist_ok=True)
    results = {"started_at": time.time(), "settings": {
        "idle_seconds": options.idle_seconds, "consecutive": options.consecutive,
        "ping_interval": options.ping_interval, "ping_duration": options.ping_duration,
        "slow_threshold_s": SLOW_THRESHOLD_S}, "profiles": []}
    def persist() -> None:
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str) + "\n",
                       encoding="utf-8")

    for profile in options.profiles:
        flushed: list = []
        try:
            if options.true_concurrency:
                results["profiles"].append(
                    probe_true_concurrency(profile, options, flushed, on_call=persist))
            elif options.concurrency:
                results["profiles"].append(
                    probe_concurrency(profile, options, flushed, on_call=persist))
            elif options.idle_ladder:
                results["profiles"].append(
                    probe_idle_ladder(profile, options.idle_ladder, options, flushed,
                                      on_call=persist))
            else:
                results["profiles"].append(
                    probe_profile(profile, options, flushed, on_call=persist))
        except Exception as exc:  # noqa: BLE001
            results["profiles"].append({"profile": profile,
                                        "fatal": f"{type(exc).__name__}: {exc}"})
        results["flushed_calls"] = results.get("flushed_calls", []) + flushed
        persist()
    results["finished_at"] = time.time()
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str) + "\n",
                   encoding="utf-8")

    for entry in results["profiles"]:
        summary = entry.get("summary")
        if entry.get("mode") == "true_concurrency":
            print(f"\n=== 真并发 {entry.get('profile')}: {json.dumps(summary, ensure_ascii=False)}")
        elif entry.get("mode") == "concurrency":
            print(f"\n=== 并发保温 {entry.get('profile')}: {json.dumps(summary, ensure_ascii=False)}")
        elif entry.get("mode") == "idle_ladder":
            print(f"\n=== 阈值 {entry.get('profile')}: {json.dumps(entry.get('ladder_results'), ensure_ascii=False)}")
        else:
            print(f"\n=== 结论 {entry.get('profile')}: {json.dumps(summary, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
