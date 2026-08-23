"""Preflight checks for WebArena services and optional auth state."""

import argparse
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen

from standard_agent.environment.webarena_config import WEBARENA_SITES


def _check_url(url: str) -> dict:
    started = time.monotonic()
    try:
        request = Request(url, headers={"User-Agent": "WebArena-baseline-health-check"})
        with urlopen(request, timeout=15) as response:
            body = response.read(200_000).decode("utf-8", errors="ignore")
            return {
                "ok": response.status < 400 and "502 Bad Gateway" not in body,
                "status": response.status,
                "latency_sec": round(time.monotonic() - started, 3),
                "body_marker": "502 Bad Gateway" not in body,
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "latency_sec": round(time.monotonic() - started, 3)}


def check_all_sites(*, require_auth: bool = False,
                    storage_state_dir: str | None = None,
                    sites: list[str] | None = None) -> dict:
    checks = {}
    selected = sites or list(WEBARENA_SITES)
    unknown = sorted(set(selected) - set(WEBARENA_SITES))
    if unknown:
        return {"ok": False, "error": f"Unknown sites: {unknown}", "sites": {}}
    for site in selected:
        config = WEBARENA_SITES[site]
        checks[site] = _check_url(config["base_url"])
        if require_auth and site in {"shopping", "shopping_admin", "gitlab"}:
            state = Path(storage_state_dir or "") / f"{site}.json"
            checks[site]["auth_state_present"] = state.is_file()
            checks[site]["ok"] = checks[site]["ok"] and state.is_file()
    return {"ok": all(check.get("ok", False) for check in checks.values()), "sites": checks}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--storage-state-dir")
    parser.add_argument("--require-auth", action="store_true")
    parser.add_argument("--sites", nargs="*", choices=sorted(WEBARENA_SITES))
    args = parser.parse_args()
    result = check_all_sites(
        require_auth=args.require_auth,
        storage_state_dir=args.storage_state_dir,
        sites=args.sites,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
