#!/usr/bin/env python3
"""Probe what an OpenAI-compatible endpoint *actually serves*, before you spend trials on it.

Why this exists
---------------
Every number in the fault matrix is conditional on the model that produced it, and
the id you put in your config is not evidence about the id that answered.  Two ways
that gap has already bitten this project:

* a third-party GPT endpoint may have been reselling a smaller model under a
  flagship name (the reason the DeepSeek re-baseline was proposed at all);
* DeepSeek's own ids alias across backends -- ``deepseek-chat`` and
  ``deepseek-reasoner`` were retired 2026-07-24 and now return errors, and
  ``deepseek-v4-pro`` was briefly slated for silent re-routing to V4.1 Flash
  during the 2026-09 retirement flip-flop, which the vendor reversed after
  user pushback.

The frozen 850-trial gpt54 artifacts record only ``model_profile: "gpt54"`` -- no
served id, no base_url, no fingerprint -- so they cannot be audited after the fact.
This script is cheap insurance against repeating that: run it before a batch, and
again if anything about an arm looks wrong.

What it checks
--------------
1. **Substitution** -- the ``model`` field the endpoint echoes back, against the id
   you requested, plus its ``system_fingerprint``.
2. **Capability floor** -- five items with exact, unambiguous answers.  A genuinely
   intact model gets them right; a heavily degraded or aggressively quantised
   backend usually does not.  These are *smoke detectors*, not a benchmark: passing
   them does not prove the endpoint is honest, it only makes some failures visible.
3. **Context handling** -- a needle buried in a large pseudo-AX-tree block, which is
   the shape of a real WebArena observation.  This catches silent truncation, which
   matters here because the agent's prompts are dominated by page text.

It deliberately does **not** try to grade model quality: no public benchmark score
can be reproduced reliably from a handful of API calls.  It answers one narrow
question -- "is the thing answering the model I think it is?" -- and answers it
without needing langgraph, langchain or a browser.

Usage
-----
    python3 scripts/probe_model_provenance.py                    # every profile in .env
    python3 scripts/probe_model_provenance.py --profile gpt54
    python3 scripts/probe_model_provenance.py --json out.json    # keep the evidence
    python3 scripts/probe_model_provenance.py --repeat 3         # catch flaky routing

Exit status is 0 only if every probe answered on every profile; a substitution or a
failed item is a non-zero exit, so this can gate a batch run in a shell chain.

Keys are read from ``.env`` / the environment and are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Probes.  Each answer is exact and stable, so a mismatch is informative rather
# than a matter of taste.  `check` receives the raw reply text.
# ---------------------------------------------------------------------------


def _norm(s: str) -> str:
    """Loose compare: case, surrounding whitespace and markdown fences ignored.

    Not looser than that on purpose -- these are exact-answer items, and
    normalising away punctuation or digits would hide exactly the sloppiness a
    degraded backend tends to display.
    """
    s = s.strip()
    s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s).strip()
    return s.lower()


PROBES: list[dict] = [
    {
        "name": "arithmetic",
        "messages": [{"role": "user", "content": "What is 847 * 293? Reply with only the number."}],
        "check": lambda r: _norm(r).replace(",", "") == "248171",
        "expect": "248171",
    },
    {
        "name": "instruction",
        "messages": [{"role": "user", "content": "Reply with exactly the word PONG and nothing else."}],
        "check": lambda r: _norm(r) == "pong",
        "expect": "PONG",
    },
    {
        "name": "multistep",
        "messages": [{
            "role": "user",
            "content": (
                "A box has 3 red and 4 blue balls. Two are drawn without replacement. "
                "What is the probability that both are red? Reply as a reduced fraction "
                "with no other text."
            ),
        }],
        "check": lambda r: _norm(r) in {"1/7", "1 / 7", "one seventh"},
        "expect": "1/7",
    },
    {
        "name": "json_shape",
        # The agent's planner/replanner parse JSON out of the reply, so a backend
        # that cannot honour a trivial JSON instruction breaks those code paths
        # for reasons unrelated to the fault being injected.
        "messages": [{
            "role": "user",
            "content": 'Output exactly this JSON and nothing else: {"ok": true}',
        }],
        "check": lambda r: _json_ok(r),
        "expect": '{"ok": true}',
    },
]


def _json_ok(reply: str) -> bool:
    try:
        return json.loads(re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", reply.strip())) == {"ok": True}
    except (json.JSONDecodeError, TypeError):
        return False


def _context_probe() -> dict:
    """A needle in a pseudo-AX-tree haystack.

    The filler mimics an Accessibility Tree dump -- the bulk of a real WebArena
    observation -- and the needle is a value that appears exactly once.  A backend
    that truncates the prompt, or that silently drops the middle of a long context,
    fails this while passing every short probe.
    """
    needle = "XK-4471-QZ"
    rows = [
        f'  <div role="listitem" id="n{i}" name="item {i} price ${i * 7 % 991}.00" />'
        for i in range(1, 900)
    ]
    rows.insert(613, f'  <div role="listitem" id="needle" name="reference code {needle}" />')
    blob = "\n".join(rows)
    return {
        "name": "long_context",
        "messages": [{
            "role": "user",
            "content": (
                "Below is an accessibility tree. Reply with only the reference code "
                "that appears in it.\n\n" + blob
            ),
        }],
        "check": lambda r: needle.lower() in _norm(r),
        "expect": needle,
    }


# ---------------------------------------------------------------------------
# .env loading -- mirrors standard_agent/config.py's naming, without importing it
# (that module pulls in langgraph, which is not needed to talk to an API).
# ---------------------------------------------------------------------------
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_env(path: Path) -> None:
    """Minimal dotenv reader.  Existing environment variables win.

    Never echoes a value -- a stray print here would leak a key into a log.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if not _ENV_KEY.match(key):
            continue
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_profiles() -> dict[str, dict]:
    """Collect MODEL_<NAME>_* into profiles, exactly as the agent does."""
    names = [n.strip() for n in os.getenv("MODEL_PROFILES", "").split(",") if n.strip()]
    single = os.getenv("MODEL_PROFILE", "")
    if not names and single and single != "default":
        names = [single]

    profiles: dict[str, dict] = {}
    for name in names:
        key = name.upper().replace("-", "_")
        profiles[name] = {
            "profile": name,
            "api_key": os.getenv(f"MODEL_{key}_API_KEY", ""),
            "base_url": os.getenv(f"MODEL_{key}_BASE_URL", ""),
            "model": os.getenv(f"MODEL_{key}_NAME", ""),
            "temperature": os.getenv(f"MODEL_{key}_TEMPERATURE", "0"),
        }
    return profiles


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _endpoint(base_url: str) -> str:
    """Append /chat/completions without doubling an existing /v1.

    DeepSeek documents both ``https://api.deepseek.com`` and ``.../v1`` as valid
    bases, so the path is appended verbatim in both cases.
    """
    return base_url.rstrip("/") + "/chat/completions"


def chat(profile: dict, messages: list, timeout: float = 180.0, max_tokens: int = 64) -> dict:
    """One non-streaming completion.  Returns the parsed body plus measured latency.

    Raises ``RuntimeError`` with the status and body on an HTTP error -- the body
    is what distinguishes "wrong model id" from "bad key" from "endpoint moved",
    and a bare status code would hide that.
    """
    payload = json.dumps({
        "model": profile["model"],
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        _endpoint(profile["base_url"]),
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {profile['api_key']}",
        },
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"connection failed: {exc.reason}") from exc
    body["_latency_s"] = round(time.time() - t0, 2)
    return body


def reply_text(body: dict) -> str:
    try:
        return str(body["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError):
        return ""


# ---------------------------------------------------------------------------
def probe_profile(profile: dict, repeat: int) -> dict:
    """Run the probe set.  Returns a report dict; never raises for a probe failure."""
    report = {
        "profile": profile["profile"],
        "requested_model": profile["model"],
        "base_url": profile["base_url"],
        "runs": [],
        "error": "",
    }
    if not (profile["api_key"] and profile["base_url"] and profile["model"]):
        report["error"] = "profile incomplete (need API_KEY, BASE_URL and NAME)"
        return report

    probes = PROBES + [_context_probe()]
    for i in range(repeat):
        run = {"probes": [], "served_model": "", "system_fingerprint": "",
               "latency_s": None, "prompt_tokens": None, "completion_tokens": None}
        for probe in probes:
            entry = {"name": probe["name"], "expect": probe["expect"]}
            try:
                body = chat(profile, probe["messages"])
                text = reply_text(body)
                entry.update({
                    "ok": bool(probe["check"](text)),
                    "reply": text.strip()[:120],
                    "error": "",
                })
                # Provenance comes off whichever call answered; all of them should
                # agree, and `--repeat` exists to notice when they do not.
                usage = body.get("usage") or {}
                if run["served_model"] == "":
                    run["served_model"] = str(body.get("model") or "")
                    run["system_fingerprint"] = str(body.get("system_fingerprint") or "")
                    run["latency_s"] = body.get("_latency_s")
                    run["prompt_tokens"] = usage.get("prompt_tokens")
                    run["completion_tokens"] = usage.get("completion_tokens")
            except Exception as exc:  # noqa: BLE001 -- report, do not abort the sweep
                entry.update({"ok": False, "reply": "", "error": str(exc)})
            run["probes"].append(entry)
        report["runs"].append(run)
    return report


def _verdict(report: dict) -> tuple[bool, list[str]]:
    """Fold a report into (ok, notes).  Notes are the human-readable findings."""
    notes: list[str] = []
    if report["error"]:
        return False, [report["error"]]

    ok = True
    served = {r["served_model"] for r in report["runs"] if r["served_model"]}
    if not served:
        ok = False
        notes.append("endpoint did not echo a served model id -- cannot verify")
    elif len(served) > 1:
        ok = False
        notes.append(f"served model id CHANGED between calls: {sorted(served)}")
    elif report["requested_model"] not in served:
        # A dated build suffix is a normal, benign answer; only say "substituted"
        # when the served id is not a variant of what was requested.
        got = next(iter(served))
        if got.startswith(report["requested_model"]):
            notes.append(f"served a dated variant: {got} (expected for pinned builds)")
        else:
            ok = False
            notes.append(f"SUBSTITUTION: asked {report['requested_model']!r}, served {got!r}")

    for run in report["runs"]:
        for probe in run["probes"]:
            if not probe["ok"]:
                ok = False
                why = probe["error"] or f"answered {probe['reply']!r}, expected {probe['expect']!r}"
                notes.append(f"{probe['name']}: {why}")
    return ok, notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--profile", action="append", default=None,
                        help="profile name to probe (repeatable); default = all in .env")
    parser.add_argument("--env", type=Path, default=REPO_ROOT / ".env",
                        help="path to the .env file (default: repo root)")
    parser.add_argument("--repeat", type=int, default=1,
                        help="calls per probe; >1 catches load-balanced or flaky routing")
    parser.add_argument("--json", type=Path, default=None,
                        help="write the full report to this path as evidence")
    args = parser.parse_args()

    load_env(args.env)
    profiles = load_profiles()
    if args.profile:
        wanted = set(args.profile)
        profiles = {k: v for k, v in profiles.items() if k in wanted}
    if not profiles:
        print(f"no model profiles found (looked in {args.env}; need MODEL_PROFILES=...)", file=sys.stderr)
        return 2

    reports, all_ok = [], True
    for name, profile in profiles.items():
        print(f"\n=== {name}  [{profile['model']} @ {profile['base_url']}] ===")
        if not profile["api_key"]:
            print("  SKIP: no API key configured for this profile")
            continue
        report = probe_profile(profile, max(1, args.repeat))
        ok, notes = _verdict(report)
        all_ok &= ok
        reports.append({"verdict_ok": ok, "notes": notes, **report})

        for run in report["runs"]:
            print(f"  served={run['served_model'] or '(none)'}  "
                  f"fp={run['system_fingerprint'] or '(none)'}  "
                  f"latency={run['latency_s']}s  "
                  f"tokens={run['prompt_tokens']}/{run['completion_tokens']}")
            for probe in run["probes"]:
                print(f"    {'PASS' if probe['ok'] else 'FAIL'}  {probe['name']}")
        print(f"  --> {'OK' if ok else 'PROBLEM'}")
        for note in notes:
            print(f"      * {note}")

    if args.json:
        args.json.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nfull report written to {args.json}")

    print(f"\n{'ALL PROFILES OK' if all_ok else 'PROBLEMS DETECTED -- do not start a batch on this'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
