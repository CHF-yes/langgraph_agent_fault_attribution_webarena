"""HTTP 服务级持续性故障（`ep-http-service-v1`，独立于 v2）。

故障对象是**站点搜索端点**，不是某个结果页 URL：`scheme + host + port + path`
确定，**query 值与额外参数完全不参与匹配**，因此换 `q`、加 `p`/排序参数仍命中同一
服务。路径使用**路径段边界**：`/search` 与 `/search/x` 命中，`/searching` 不命中。

仍是观察层模拟：真实导航已执行，HAR 可能是 200，真实副作用没有撤销。
"""

from __future__ import annotations

import time
from urllib.parse import urlsplit

EXPERIMENT_ID = "ep-http-service-v1"
TARGET_POLICY_VERSION = "endpoint-path-segment-v1"
VARIANTS = ("single", "persistent")
DEFAULT_WINDOW_STEPS = 10
NAVIGATION_ACTIONS = {"goto", "click", "go_back", "go_forward", "type_text", "select_option"}
DEFAULT_PORTS = {"http": 80, "https": 443}


def _normalize_path(path: str) -> str:
    path = path or "/"
    if not path.startswith("/"):
        path = "/" + path
    if path != "/":
        path = path.rstrip("/") or "/"
    return path


class ServiceEndpoint:
    """确定性的端点身份与匹配。"""

    def __init__(self, *, scheme: str, host: str, port, path: str):
        scheme = str(scheme or "").lower()
        if scheme not in DEFAULT_PORTS:
            raise ValueError("endpoint scheme must be http or https")
        host = str(host or "").strip().lower()
        if not host:
            raise ValueError("endpoint host is required")
        if port in (None, ""):
            port = DEFAULT_PORTS[scheme]
        try:
            port = int(port)
        except (TypeError, ValueError):
            raise ValueError("endpoint port must be an integer") from None
        if not 1 <= port <= 65535:
            raise ValueError("endpoint port out of range")
        self.scheme, self.host, self.port = scheme, host, port
        self.path = _normalize_path(path)

    @classmethod
    def from_spec(cls, spec: dict) -> "ServiceEndpoint":
        if not isinstance(spec, dict):
            raise ValueError("endpoint spec must be a mapping")
        for key in ("scheme", "host", "path"):
            if key not in spec:
                raise ValueError(f"endpoint spec missing {key}")
        return cls(scheme=spec["scheme"], host=spec["host"],
                   port=spec.get("port"), path=spec["path"])

    @classmethod
    def from_url(cls, url: str) -> "ServiceEndpoint":
        parsed = urlsplit(str(url or "").strip())
        if parsed.scheme not in DEFAULT_PORTS or not parsed.hostname:
            raise ValueError("endpoint URL must be absolute http(s)")
        return cls(scheme=parsed.scheme, host=parsed.hostname,
                   port=parsed.port, path=parsed.path or "/")

    def matches(self, url: str) -> tuple[bool, dict]:
        parsed = urlsplit(str(url or "").strip())
        path = _normalize_path(parsed.path or "/")
        port = parsed.port if parsed.port is not None else DEFAULT_PORTS.get(parsed.scheme)
        in_path = (path == self.path if self.path == "/"
                   else path == self.path or path.startswith(self.path + "/"))
        matched = (parsed.scheme == self.scheme
                   and (parsed.hostname or "").lower() == self.host
                   and port == self.port and in_path)
        return matched, {"scheme": parsed.scheme, "host": parsed.hostname, "port": port,
                         "path": path, "query": parsed.query}

    def identity(self) -> dict:
        return {"kind": "endpoint", "scheme": self.scheme, "host": self.host,
                "port": self.port, "path": self.path,
                "target_policy_version": TARGET_POLICY_VERSION}

    def spec(self) -> dict:
        return {"scheme": self.scheme, "host": self.host, "port": self.port,
                "path": self.path, "target_policy_version": TARGET_POLICY_VERSION}


def protocol_metadata(config) -> dict:
    return {
        "mechanism": "synthetic_http_observation_replacement",
        "trigger_policy": TARGET_POLICY_VERSION,
        "target_policy_version": TARGET_POLICY_VERSION,
        "fault_target_endpoint": config.endpoint.spec(),
        "window_steps": config.fault_window_steps,
        "window_unit": "proxy_execution_step",
        "bootstrap_navigation_faulted": False,
        "retarget": False,
        "query_matching": "ignored",
    }


class ServiceEpisode:
    def __init__(self, variant: str, *, window_steps: int):
        if variant not in VARIANTS:
            raise ValueError(f"{EXPERIMENT_ID} only supports {VARIANTS}")
        if not isinstance(window_steps, int) or isinstance(window_steps, bool) or window_steps < 1:
            raise ValueError("fault_window_steps must be >= 1")
        self.variant, self.window_steps = variant, window_steps
        self.armed = False
        self.armed_after_step = None
        self.target = None
        self.t0 = None
        self.injections = 0
        self.released = None
        self.events: list = []
        self.injected_actions: set = set()

    @property
    def window_end(self):
        return None if self.t0 is None else self.t0 + self.window_steps - 1

    def note_step(self, step):
        if self.t0 is not None and step > self.window_end and self.released is None:
            self.released = "window_expired"
            self.events.append({"event": "release", "proxy_step": step,
                                "reason": self.released})

    def record_skip(self, *, proxy_step, action_uid, reason, detail=None):
        self.events.append({"event": "skip", "proxy_step": proxy_step,
                            "action_uid": action_uid, "reason": reason,
                            "detail": detail or {}})

    def record_injection(self, *, proxy_step, action_uid, target, error_type,
                         match=None):
        if self.t0 is None:
            self.t0 = proxy_step
            self.target = dict(target)
        self.injections += 1
        self.injected_actions.add(action_uid)
        self.events.append({
            "event": "injection", "experiment_id": EXPERIMENT_ID,
            "variant": self.variant, "injection_index": self.injections,
            "proxy_step": proxy_step, "action_uid": action_uid,
            "target": dict(self.target), "error_type": error_type,
            "match": match or {}, "timestamp": time.time(),
        })
        if self.variant == "single":
            self.released = "single_completed"

    def snapshot(self):
        return {"experiment_id": EXPERIMENT_ID, "variant": self.variant,
                "target_policy_version": TARGET_POLICY_VERSION,
                "armed": self.armed, "armed_after_step": self.armed_after_step,
                "target": self.target, "t0": self.t0,
                "window_steps": self.window_steps, "window_end": self.window_end,
                "injections": self.injections, "released": self.released,
                "exposure_class": ("not_triggered" if not self.injections else
                                   "once" if self.injections == 1 else "repeated")}


class ServicePersistenceEngine:
    def __init__(self, config):
        self.config = config
        self.endpoint: ServiceEndpoint = config.endpoint
        self.episode: ServiceEpisode = config.episode
        self.error_type = None
        self.last_decision = {}

    def arm(self, proxy_step):
        self.episode.armed = True
        self.episode.armed_after_step = proxy_step

    def _record(self, *, proxy_step, action_uid, target, detail):
        self.episode.record_injection(proxy_step=proxy_step, action_uid=action_uid,
                                      target=target, error_type=self.error_type,
                                      match=detail)
        self.config.record_injection("web_http_error", {
            "mechanism": "synthetic_http_observation_replacement",
            "endpoint": self.endpoint.spec(), "error_type": self.error_type,
            "action_uid": action_uid, "proxy_step": proxy_step})

    def apply_http(self, obs, *, proxy_step, action_uid, action, previous_url,
                   allow_activation=True):
        ep = self.episode
        ep.note_step(proxy_step)
        raw_url = str(obs.get("url", "") or "")
        matched, match = self.endpoint.matches(raw_url) if raw_url else (False, {})
        self.last_decision = {"raw_url": raw_url, "matched_endpoint": matched,
                              "endpoint": self.endpoint.spec(), "match": match}

        def clean(reason):
            self.last_decision.update(reason=reason, fault_delivered=False)
            ep.record_skip(proxy_step=proxy_step, action_uid=action_uid, reason=reason,
                           detail={"raw_url": raw_url, "match": match})
            return obs

        if not self.config.enabled or self.config.intensity == "off" or not self.config.web_http_error:
            return clean("control_or_disabled")
        if not ep.armed:
            return clean("not_armed")
        if proxy_step <= ep.armed_after_step:
            return clean("bootstrap_observation")

        if ep.target is None:
            if not allow_activation or not action_uid or action not in NAVIGATION_ACTIONS:
                return clean("not_navigation")
            previous = str(previous_url or "")
            if action != "goto" and raw_url.split("#", 1)[0] == previous.split("#", 1)[0]:
                return clean("not_navigation")
            if (action == "goto" and raw_url.split("#", 1)[0] == previous.split("#", 1)[0]
                    and raw_url != previous):
                return clean("fragment_only_navigation")
            if not matched:
                return clean("outside_endpoint")
            if not str(obs.get("page_content") or "").strip():
                return clean("empty_observation")
            self.error_type = self.config.rng.pick(["500", "503"])
        else:
            if not matched:
                return clean("off_endpoint")
            if action_uid not in ep.injected_actions and ep.released:
                return clean(ep.released)

        if action_uid not in ep.injected_actions:
            self._record(proxy_step=proxy_step, action_uid=action_uid,
                         target=self.endpoint.identity(), detail=match)
        self.last_decision.update(reason="injected_or_same_action_replay",
                                  fault_delivered=True)
        label = ("500 Internal Server Error" if self.error_type == "500"
                 else "503 Service Unavailable")
        return {"url": obs.get("url", ""),
                "page_content": f"[1] heading '{label}'\n[2] text 'The requested resource is unavailable.'",
                "elements": {}}


def classify_service_episode(payload, *, expected_variant=None, expected_window=None):
    errors: list[str] = []
    variant = payload.get("variant")
    window = payload.get("window_steps")
    entries = payload.get("injections")
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict):
        errors.append("missing snapshot")
        snapshot = {}
    if payload.get("experiment_id") != EXPERIMENT_ID:
        errors.append("wrong experiment_id")
    if variant not in VARIANTS or (expected_variant and variant != expected_variant):
        errors.append("wrong variant")
    if not isinstance(window, int) or isinstance(window, bool) or window < 1:
        errors.append("invalid window_steps")
    if expected_window is not None and window != expected_window:
        errors.append("window_steps mismatch")
    if not isinstance(entries, list):
        errors.append("missing injections")
        entries = []
    if variant == "single" and len(entries) > 1:
        errors.append("single injected more than once")
    if snapshot.get("target_policy_version") != TARGET_POLICY_VERSION:
        errors.append("target policy mismatch")

    seen: set = set()
    first_step = None
    target = None
    previous_step = None
    error_type = None
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            errors.append("invalid injection entry")
            continue
        uid, step = entry.get("action_uid"), entry.get("proxy_step")
        if not isinstance(uid, str) or not uid or uid in seen:
            errors.append("missing or repeated action_uid")
        seen.add(str(uid))
        if not isinstance(step, int) or isinstance(step, bool) or step < 1:
            errors.append("invalid proxy_step")
            continue
        if first_step is None:
            first_step, target, error_type = step, entry.get("target"), entry.get("error_type")
        if not isinstance(target, dict) or target.get("kind") != "endpoint" \
                or entry.get("target") != target:
            errors.append("missing or changed locked endpoint")
        elif not all(target.get(k) for k in ("scheme", "host", "port", "path")):
            errors.append("incomplete endpoint identity")
        if previous_step is not None and step <= previous_step:
            errors.append("injection steps not strictly increasing")
        previous_step = step
        if error_type not in {"500", "503"} or entry.get("error_type") != error_type:
            errors.append("missing or changed error_type")
        if isinstance(window, int) and not first_step <= step < first_step + window:
            errors.append("injection outside window")
        if entry.get("injection_index") != index:
            errors.append("injection_index mismatch")

    if snapshot.get("injections") != len(entries) or snapshot.get("variant") != variant:
        errors.append("snapshot mismatch")
    if snapshot.get("t0") != first_step or snapshot.get("target") != target:
        errors.append("snapshot target/t0 mismatch")
    if snapshot.get("window_steps") != window:
        errors.append("snapshot window mismatch")
    if entries and (not snapshot.get("armed")
                    or not isinstance(snapshot.get("armed_after_step"), int)
                    or first_step <= snapshot["armed_after_step"]):
        errors.append("injection before agent activation")

    released = snapshot.get("released")
    t0, window_end = snapshot.get("t0"), snapshot.get("window_end")
    if isinstance(t0, int) and isinstance(window, int) and window_end != t0 + window - 1:
        errors.append("snapshot window_end mismatch")
    if variant == "single" and entries:
        if released != "single_completed":
            errors.append("single release reason mismatch")
    elif variant == "persistent" and released not in (None, "window_expired"):
        errors.append("invalid persistent release reason")

    events = [e for e in (payload.get("events") or []) if isinstance(e, dict)]
    if events:
        release_events = [e for e in events if e.get("event") == "release"]
        if isinstance(window_end, int):
            late = [e for e in events
                    if isinstance(e.get("proxy_step"), int) and e["proxy_step"] > window_end]
            if late and released is None:
                errors.append("events after window end without release state")
        if release_events:
            if released is None:
                errors.append("release event with open snapshot")
            for event in release_events:
                if event.get("reason") != released:
                    errors.append("release reason mismatch")
                step = event.get("proxy_step")
                if not isinstance(step, int) or (isinstance(window_end, int) and step <= window_end):
                    errors.append("release event not after window end")
        if released == "window_expired" and not release_events:
            errors.append("window_expired without release event")

    return {"valid": not errors, "errors": errors, "count": len(entries),
            "variant": variant}
