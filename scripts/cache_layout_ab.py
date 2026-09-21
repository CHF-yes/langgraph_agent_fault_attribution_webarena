#!/usr/bin/env python3
"""Standalone prompt-cache layout diagnostic.  NOT part of the agent harness.

This tool answers one narrow question with HTTP requests only: when the same
information is sent in two different message orders, how much of it does the
endpoint's prefix cache actually reuse?  It never drives the agent, never calls
a tool, and nothing in ``standard_agent/`` imports it.

The two layouts compared
------------------------
``A`` mirrors the current harness ordering (``standard_agent/core/nodes.py``):
the volatile page observation is interpolated *into the first system message*,
ahead of the long stable instruction tail.

``B`` keeps the system prompt static and appends the volatile observation as the
newest message at the end of the conversation.

What this tool does and does not establish
------------------------------------------
It establishes: per-request prompt / cache-hit / cache-miss / completion token
counts, latency, and how the reusable prefix changes with conversation length.

It does **not** establish that the two layouts are equivalent to the model.
Identical message *text* (verified below, including a SHA-256 over the sorted
segments) is not the same as identical message *roles*, *boundaries*, or an
identical ``tools`` payload.  The tool therefore reports the role sequence and
the message-boundary count for each layout instead of implying equivalence, and
moving an observation to the end changes what the model sees first and how
"fresh" the newest observation looks -- a behavioural question this tool cannot
answer.  A layout change needs a full-task validation before it can be adopted.

Reproducibility controls
------------------------
* ``--salt`` mixes a run-specific token into the shared static prefix, so a run
  starts from a prefix earlier runs cannot have warmed.  The salt is identical
  in both layouts, so the information comparison stays valid.
* ``--order ab|ba|alternate`` controls which layout is sent first inside every
  (step, repetition).  A fixed A-then-B order can bias the delta through
  warm-up, so check the reversal.
* ``--tools fixed|none`` includes a fixed tool-schema array in the request,
  because tool definitions travel outside ``messages`` and may themselves be
  part of the cached prefix.

Usage
-----
    python3 scripts/cache_layout_ab.py --steps 5 --repeats 2 --order ab
    python3 scripts/cache_layout_ab.py --steps 5 --repeats 2 --order ba --salt run2
    python3 scripts/cache_layout_ab.py --steps 5 --repeats 2 \
        --out-dir experiments/cache_layout_ab/pilot \
        --hit-price 0.07 --miss-price 0.27 --output-price 1.10

Outputs, under ``--out-dir`` (default: a timestamped directory below
``experiments/cache_layout_ab/``):
    requests.jsonl   one record per request (tokens, latency, timestamp)
    summary.json     machine-readable aggregate
    SUMMARY.md       de-keyed human summary safe to share
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

TASK = ("Get name(s) of reviewer(s) who mention complain of the customer "
        "service for the product on the current page")
URL_BASE = ("http://localhost:7770/epson-workforce-wf-3620-wifi-direct-all-in-one"
            "-color-inkjet-printer-copier-scanner-amazon-dash-replenishment-ready.html")

SEG_TASK_MSG = f"Please continue completing this task: {TASK}"
REMINDER = ("REMINDER: Your reply MUST begin with 'THOUGHT:' followed by your "
            "reasoning, and ONLY THEN call a tool. Calling a tool with empty text "
            "content is a protocol violation.")

# A fixed tool schema array, shaped like the real tool set.  Sent unchanged with
# both layouts; it matters only because schemas are part of the request prefix.
TOOLS = [
    {"type": "function", "function": {
        "name": name, "description": f"{name} on the page",
        "parameters": {"type": "object", "properties": {
            "element_id": {"type": "string"}, "text": {"type": "string"},
            "direction": {"type": "string"}}, "required": []}}}
    for name in ("click", "type_text", "scroll", "goto", "go_back", "go_forward",
                 "select_option", "hover", "stop")
]


def build_sys(salt: str) -> str:
    """The whole static instruction block, salted so the run starts cold."""
    return (
        "You are a standard ReAct (Reasoning + Acting) web automation agent "
        "completing tasks on real websites.\n"
        f"[run-corpus={salt}]\n\n"
        "## Your Task\n"
        f"{TASK}\n\n"
        "## Available Actions\n"
        "You can use the following tools to interact with the page:\n"
        + "".join(f"- **tool_{i}(arg)**: description of tool {i}.\n" for i in range(80))
        + "\n## ReAct Protocol (MANDATORY)\n"
        "1. THOUGHT (REQUIRED, NEVER SKIP): the text content of your reply MUST begin\n"
        "   with \"THOUGHT:\" and explain what you know and why.\n"
        "2. ACTION (REQUIRED): call exactly ONE tool.\n"
        "   Calling a tool with EMPTY text content is a PROTOCOL VIOLATION.\n"
        "### Example (one step)\n"
        "THOUGHT: The product is visible with price [100]; I can answer directly.\n"
    )


def seg_obs(step: int) -> str:
    """Volatile observation for a step: URL + AX Tree.

    Every line depends on ``step``.  A corpus where step k is merely a longer
    version of step k-1 would let the "observation early" layout keep a long
    shared prefix, which measures the corpus rather than the layout.
    """
    lines = ["[1] banner", "  [2] link 'One Stop Market'", "  [3] form 'Search'"]
    for i in range(4, 140 + 20 * step):
        kind = ("text", "link", "listitem", "heading", "img", "button")[i % 6]
        lines.append(f"  [{i}] {kind} 'step {step} view entry {i} for the product "
                     f"page, rating {i % 6}, body text about customer service'")
    return (f"\n## Current Page\n- URL: {URL_BASE}\n"
            f"- Accessibility Tree (AX Tree):\n" + "\n".join(lines) + "\n")


def history_upto(step: int) -> list[dict]:
    msgs: list[dict] = []
    for s in range(1, step):
        msgs.append({"role": "assistant", "content":
                     f"THOUGHT: step {s} reasoning about the page state and the next "
                     f"action to take; checking review entry {s}."})
        msgs.append({"role": "user", "content":
                     f"[OK] action from step {s} executed; page updated.\n"
                     + "\n".join(f"  [{i}] text 'observation {s}-{i}'" for i in range(1, 60))})
    return msgs


def layout_a(salt: str, step: int) -> list[dict]:
    return ([{"role": "system", "content": build_sys(salt) + seg_obs(step)},
             {"role": "user", "content": SEG_TASK_MSG}]
            + history_upto(step)
            + [{"role": "system", "content": REMINDER}])


def layout_b(salt: str, step: int) -> list[dict]:
    return ([{"role": "system", "content": build_sys(salt)},
             {"role": "user", "content": SEG_TASK_MSG}]
            + history_upto(step)
            + [{"role": "user", "content": seg_obs(step)},
               {"role": "system", "content": REMINDER}])


def assert_same_text(salt: str, step: int, a: list[dict], b: list[dict]) -> dict:
    """Verify identical text; report -- do not imply -- role/boundary equality."""
    sys_block = build_sys(salt)
    obs = seg_obs(step)
    assert a[0]["content"] == sys_block + obs, f"step {step}: A head unexpected"
    assert b[0]["content"] == sys_block, f"step {step}: B head unexpected"
    assert b[-2]["content"] == obs, f"step {step}: B observation not last-but-one"

    def expand(msgs: list[dict]) -> list[str]:
        out: list[str] = []
        for m in msgs:
            out += [sys_block, obs] if m["content"] == sys_block + obs else [m["content"]]
        return out

    ta, tb = sorted(expand(a)), sorted(expand(b))
    assert ta == tb, f"step {step}: segment multisets differ"
    ha = hashlib.sha256("".join(ta).encode()).hexdigest()
    hb = hashlib.sha256("".join(tb).encode()).hexdigest()
    assert ha == hb, f"step {step}: text hash differs"
    return {
        "step": step, "text_sha256": ha, "chars": sum(len(x) for x in ta),
        "a_messages": len(a), "b_messages": len(b),
        "a_roles": [m["role"] for m in a], "b_roles": [m["role"] for m in b],
        # Explicitly NOT claimed equal by this tool:
        "role_or_boundary_equivalence": "not established by this tool",
    }


def resolve_profile(name: str) -> tuple[str, str, str]:
    from standard_agent.config import settings  # noqa: PLC0415
    profile = settings.get_model_profile(name)
    if not (profile.api_key and profile.base_url and profile.model):
        raise SystemExit(f"profile {name!r} is not fully configured in .env")
    return profile.api_key, profile.base_url, profile.model


def chat(messages: list[dict], tools: list[dict] | None, key: str, base: str,
         model: str, max_tokens: int) -> dict:
    payload: dict = {"model": model, "messages": messages,
                     "temperature": 0, "max_tokens": max_tokens}
    if tools:
        payload["tools"] = tools
    req = urllib.request.Request(
        base.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    started = time.time()
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = json.loads(resp.read().decode())
    usage = body.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    hit = usage.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = details.get("cached_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    if hit is not None and miss is None and usage.get("prompt_tokens") is not None:
        miss = max(int(usage["prompt_tokens"]) - int(hit), 0)
    return {
        "prompt_tokens": usage.get("prompt_tokens"),
        "cache_hit_tokens": hit,
        "cache_miss_tokens": miss,
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "latency_s": round(time.time() - started, 3),
        "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def main() -> int:  # noqa: C901
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model-profile", default="deepseek_v4_pro")
    ap.add_argument("--steps", type=int, default=6)
    ap.add_argument("--repeats", type=int, default=3,
                    help="1 cold send + (repeats-1) warm resends per (layout, step)")
    ap.add_argument("--order", choices=("ab", "ba", "alternate"), default="ab",
                    help="layout order inside each (step, repetition)")
    ap.add_argument("--salt", default=None,
                    help="run-level corpus token; default: random per run")
    ap.add_argument("--tools", choices=("fixed", "none"), default="fixed")
    ap.add_argument("--max-tokens", type=int, default=16)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--hit-price", type=float, default=None, help="currency per 1M cache-hit tokens")
    ap.add_argument("--miss-price", type=float, default=None, help="currency per 1M cache-miss tokens")
    ap.add_argument("--output-price", type=float, default=None, help="currency per 1M output tokens")
    ap.add_argument("--dry-run", action="store_true",
                    help="verify the text-identity assertion for every step and exit")
    args = ap.parse_args()

    salt = args.salt or f"r{random.randint(100000, 999999)}"
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out_dir) if args.out_dir else \
        REPO / "experiments/cache_layout_ab" / f"{stamp}-{args.order}-{salt}"
    tools = TOOLS if args.tools == "fixed" else None

    checks = [assert_same_text(salt, k, layout_a(salt, k), layout_b(salt, k))
              for k in range(1, args.steps + 1)]
    print(f"salt={salt} order={args.order} tools={args.tools}")
    print(f"text-identity assertion: PASS over {len(checks)} steps "
          f"({checks[0]['chars']:,} chars at step 1; "
          f"A {checks[0]['a_messages']} msgs vs B {checks[0]['b_messages']} msgs)")
    print("note: identical text does NOT establish role/boundary/tool equivalence")
    if args.dry_run:
        return 0

    key, base, model = resolve_profile(args.model_profile)
    out_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict] = []
    with (out_dir / "requests.jsonl").open("w") as fh:
        for k in range(1, args.steps + 1):
            built = {"A": layout_a(salt, k), "B": layout_b(salt, k)}
            for rep in range(1, args.repeats + 1):
                if args.order == "ab":
                    order = ["A", "B"]
                elif args.order == "ba":
                    order = ["B", "A"]
                else:
                    order = ["A", "B"] if rep % 2 else ["B", "A"]
                for name in order:
                    phase = "cold" if rep == 1 else f"warm{rep - 1}"
                    try:
                        got = chat(built[name], tools, key, base, model, args.max_tokens)
                    except urllib.error.HTTPError as exc:
                        got = {"error": f"HTTP {exc.code}: {exc.read().decode()[:160]}"}
                    except Exception as exc:  # noqa: BLE001
                        got = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
                    rec = {"layout": name, "step": k, "rep": rep, "phase": phase,
                           "model": model, "messages": len(built[name]),
                           "chars": sum(len(m["content"]) for m in built[name]),
                           "roles": [m["role"] for m in built[name]],
                           "tools": len(tools or [])}
                    rec.update(got)
                    records.append(rec)
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    pt, hit = rec.get("prompt_tokens"), rec.get("cache_hit_tokens")
                    rate = f"{hit / pt * 100:5.1f}%" if pt and hit is not None else "  n/a"
                    print(f"  k={k:>2} {name} {phase:<5} prompt={pt} hit={hit} "
                          f"miss={rec.get('cache_miss_tokens')} hit%={rate} "
                          f"{rec.get('latency_s')}s"
                          + (f"  {rec['error']}" if "error" in rec else ""), flush=True)

    def total(sel: list[dict], field: str) -> int:
        return sum(int(r.get(field) or 0) for r in sel)

    summary: dict = {"salt": salt, "order": args.order, "model": model,
                     "tools": len(tools or []), "steps": args.steps,
                     "repeats": args.repeats, "cells": [], "checks": checks}
    for name in ("A", "B"):
        for phase in ["cold"] + [f"warm{i}" for i in range(1, args.repeats)]:
            sel = [r for r in records
                   if r["layout"] == name and r["phase"] == phase and "error" not in r]
            if not sel:
                continue
            pt = total(sel, "prompt_tokens")
            hit = total(sel, "cache_hit_tokens")
            cell = {"layout": name, "phase": phase, "calls": len(sel),
                    "prompt_tokens": pt, "cache_hit_tokens": hit,
                    "cache_miss_tokens": total(sel, "cache_miss_tokens"),
                    "completion_tokens": total(sel, "completion_tokens"),
                    "hit_rate": round(hit / pt, 4) if pt else None,
                    "mean_latency_s": round(
                        sum(r["latency_s"] for r in sel) / len(sel), 3)}
            if args.hit_price is not None and args.miss_price is not None \
                    and args.output_price is not None:
                cell["cost"] = round(
                    cell["cache_hit_tokens"] / 1e6 * args.hit_price
                    + cell["cache_miss_tokens"] / 1e6 * args.miss_price
                    + cell["completion_tokens"] / 1e6 * args.output_price, 6)
            summary["cells"].append(cell)

    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    lines = [f"# Prompt-cache layout A/B ({stamp})", "",
             f"- model: `{model}` (profile `{args.model_profile}`)",
             f"- order: `{args.order}`, tools: {len(tools or [])}, "
             f"steps: {args.steps}, repeats: {args.repeats}, salt: `{salt}`",
             "- text-identity assertion: PASS (identical text; role/boundary "
             "equivalence is **not** established by this tool)", "",
             "| layout | phase | calls | prompt | hit | miss | hit% | mean latency s |",
             "|---|---|---|---|---|---|---|---|"]
    for c in summary["cells"]:
        hr = "n/a" if c["hit_rate"] is None else f"{c['hit_rate'] * 100:.1f}%"
        lines.append(f"| {c['layout']} | {c['phase']} | {c['calls']} | "
                     f"{c['prompt_tokens']:,} | {c['cache_hit_tokens']:,} | "
                     f"{c['cache_miss_tokens']:,} | {hr} | {c['mean_latency_s']} |")
    lines += ["", "## Limits", "",
              "- Request-level measurement only. It does not show that either layout "
              "preserves agent actions, termination, or task success.",
              "- Identical text is not identical roles, boundaries, or `tools` payload; "
              "moving an observation changes what the model sees first.",
              "- Read the `cold` rows: warm resends reuse an identical prompt and hit "
              "by construction, so they cannot separate the layouts.",
              "- Absolute hit rates are only interpretable together with `--salt`; "
              "an unsalted run can inherit warmth from earlier traffic."]
    (out_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n")

    print("\n=== summary ===")
    for c in summary["cells"]:
        hr = "n/a" if c["hit_rate"] is None else f"{c['hit_rate'] * 100:.1f}%"
        extra = f" cost={c['cost']}" if "cost" in c else ""
        print(f"  {c['layout']} {c['phase']:<5} calls={c['calls']:>2} "
              f"prompt={c['prompt_tokens']:>9,} hit={c['cache_hit_tokens']:>9,} "
              f"miss={c['cache_miss_tokens']:>9,} hit%={hr}{extra}")
    print(f"\nwrote {out_dir}/requests.jsonl, summary.json, SUMMARY.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
