"""Event-triggered, same-resource HTTP observation faults (a separate v2 protocol).

This simulates resource unavailability in observations; it does not intercept HTTP
traffic or undo browser side effects. Initial navigation is clean. The caller
explicitly arms the engine before handing control to the agent.
"""

from __future__ import annotations

import time
from urllib.parse import urlsplit

from fault_injection.targets import normalize_url

EXPERIMENT_ID = "ep-http-persistence-v2"
TARGET_POLICY_VERSION = "first-agent-navigation-url-lock-v2"
VARIANTS = ("single", "persistent")
DEFAULT_WINDOW_STEPS = 10
NAVIGATION_ACTIONS = {"goto", "click", "go_back", "go_forward", "type_text", "select_option"}


def resource_url(url: str) -> str:
    value = normalize_url(url)
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("HTTP resource target must be an absolute http(s) URL")
    return value


def protocol_metadata(config) -> dict:
    return {
        "mechanism": "synthetic_http_observation_replacement",
        "trigger_policy": TARGET_POLICY_VERSION,
        "target_policy_version": TARGET_POLICY_VERSION,
        "fault_target_url": config.fault_target_url,
        "window_steps": config.fault_window_steps,
        "window_unit": "proxy_execution_step",
        "bootstrap_navigation_faulted": False,
        "retarget": False,
    }


class HttpResourceEpisode:
    def __init__(self, variant: str, *, window_steps: int):
        if variant not in VARIANTS:
            raise ValueError(f"{EXPERIMENT_ID} only supports {VARIANTS}")
        if not isinstance(window_steps, int) or isinstance(window_steps, bool) or window_steps < 1:
            raise ValueError("fault_window_steps must be >= 1")
        self.variant = variant
        self.window_steps = window_steps
        self.armed = False
        self.armed_after_step = None
        self.target = None
        self.t0 = None
        self.injections = 0
        self.released = None
        self.events = []
        self.injected_actions = set()

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

    def record_injection(self, *, proxy_step, action_uid, target, error_type):
        if self.t0 is None:
            self.t0 = proxy_step
            self.target = {"kind": "http", "page_url": target}
        self.injections += 1
        self.injected_actions.add(action_uid)
        self.events.append({
            "event": "injection", "experiment_id": EXPERIMENT_ID,
            "variant": self.variant, "injection_index": self.injections,
            "proxy_step": proxy_step, "action_uid": action_uid,
            "target": dict(self.target), "error_type": error_type,
            "timestamp": time.time(),
        })
        if self.variant == "single":
            self.released = "single_completed"

    def snapshot(self):
        return {
            "experiment_id": EXPERIMENT_ID, "variant": self.variant,
            "target_policy_version": TARGET_POLICY_VERSION,
            "armed": self.armed, "armed_after_step": self.armed_after_step,
            "target": self.target, "t0": self.t0,
            "window_steps": self.window_steps, "window_end": self.window_end,
            "injections": self.injections, "released": self.released,
            # Exposure count is descriptive, not a prescribed three-dose target.
            "exposure_class": ("not_triggered" if not self.injections else
                               "once" if self.injections == 1 else "repeated"),
        }


class HttpPersistenceEngine:
    def __init__(self, config):
        self.config = config
        self.episode = config.episode
        self.error_type = None
        self.last_decision = {}

    def arm(self, proxy_step):
        self.episode.armed = True
        self.episode.armed_after_step = proxy_step

    def apply_http(self, obs, *, proxy_step, action_uid, action, previous_url,
                   allow_activation=True):
        ep = self.episode
        ep.note_step(proxy_step)
        raw_url = normalize_url(obs.get("url", ""))
        target_url = (ep.target or {}).get("page_url")
        self.last_decision = {"raw_url": raw_url, "target_url": target_url,
                              "matches_target": bool(target_url and raw_url == target_url)}

        def clean(reason):
            self.last_decision.update(reason=reason, fault_delivered=False)
            ep.record_skip(proxy_step=proxy_step, action_uid=action_uid, reason=reason,
                           detail={"raw_url": raw_url})
            return obs

        if not self.config.enabled or self.config.intensity == "off" or not self.config.web_http_error:
            return clean("control_or_disabled")
        if not ep.armed:
            return clean("not_armed")
        if proxy_step <= ep.armed_after_step:
            return clean("bootstrap_observation")
        if ep.target is None:
            # Passive observation cannot activate a fault. A click only qualifies
            # when it changes resource URL; explicit goto also permits refreshes.
            if not allow_activation or not action_uid or action not in NAVIGATION_ACTIONS:
                return clean("not_navigation")
            if action != "goto" and raw_url == normalize_url(previous_url):
                return clean("not_navigation")
            if (action == "goto" and raw_url == normalize_url(previous_url)
                    and obs.get("url") != previous_url):
                return clean("fragment_only_navigation")
            try:
                resource_url(raw_url)
            except ValueError:
                return clean("not_http_resource")
            if not str(obs.get("page_content") or "").strip():
                return clean("empty_observation")
            if self.config.fault_target_url and raw_url != self.config.fault_target_url:
                return clean("outside_target_filter")
            self.error_type = self.config.rng.pick(["500", "503"])
        else:
            if raw_url != target_url:
                return clean("off_target")
            # Fresh reads of an already faulted action keep the same view even
            # in single. They never create another injection or clean bypass.
            if action_uid not in ep.injected_actions and ep.released:
                return clean(ep.released)

        if action_uid not in ep.injected_actions:
            ep.record_injection(proxy_step=proxy_step, action_uid=action_uid,
                                target=raw_url, error_type=self.error_type)
            self.config.record_injection("web_http_error", {
                "mechanism": "synthetic_http_observation_replacement",
                "original_url": raw_url, "error_type": self.error_type,
                "action_uid": action_uid, "proxy_step": proxy_step,
            })
        self.last_decision.update(target_url=ep.target["page_url"], matches_target=True,
                                  reason="injected_or_same_action_replay", fault_delivered=True)
        # Keep the resource URL and expose no fictitious clickable retry controls.
        label = "500 Internal Server Error" if self.error_type == "500" else "503 Service Unavailable"
        return {"url": obs.get("url", ""),
                "page_content": f"[1] heading '{label}'\n[2] text 'The requested resource is unavailable.'",
                "elements": {}}


def classify_episode(payload, *, expected_variant=None, expected_window=None):
    """Validate protocol evidence without selecting trials by realized dose."""
    errors = []
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
    seen = set()
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
            first_step, target = step, entry.get("target")
            error_type = entry.get("error_type")
        if (entry.get("target") != target or not isinstance(target, dict)
                or not target.get("page_url")):
            errors.append("missing or changed locked target")
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
    if snapshot.get("target_policy_version") != TARGET_POLICY_VERSION:
        errors.append("target policy mismatch")
    if entries and (not snapshot.get("armed") or not isinstance(snapshot.get("armed_after_step"), int)
                    or first_step is None or first_step <= snapshot["armed_after_step"]):
        errors.append("injection before agent activation")
    return {"valid": not errors, "errors": errors, "count": len(entries), "variant": variant}
