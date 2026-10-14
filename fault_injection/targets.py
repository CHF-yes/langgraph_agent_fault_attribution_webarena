"""E-P 目标匹配策略（版本化、纯逻辑）。

`repeat3` 的全部可证伪性都落在这里：故障必须**锁定同一个语义目标**，在页面刷新、
AX 数字 id 变化之后仍能重新识别它；识别不到或有歧义时只记录并跳过，绝不换目标凑剂量。

可用字段（来自 ``standard_agent/environment/browser.py``）：
    element_map: {eid: {"role": str, "name": str, "attrs": dict, "role_index": int}}
其中 ``attrs`` 在链接上可能带 ``url``（即 href）。

规范化与判定：
* 页面 URL：**只移除 fragment**，保留 scheme/host/port/path/query（不合并分页与不同资源）；
* 文本：折叠空白并 strip，比较区分大小写（不做 casefold，避免把不同条目判成同一个）；
* 目标身份 = (kind, page_url, role, name, href|None)；参数动作再带 action 类型；
* 匹配：先按页面 URL 与 (role,name,href) 取候选；0 个 → ``absent``；
  1 个 → ``ok``；多个 → ``ambiguous``（**不使用 role_index 消歧**，该序号会随刷新改变）。

``role_index`` 只记录在身份里供日志阅读，判定时不参与匹配。
版本号在实现通过离线 fixture 后冻结；改动判定逻辑必须同时升版本号。
"""

from __future__ import annotations

import re

TARGET_POLICY_VERSION = "semantic-target-lock-v1"
TARGET_POLICY_NAME = "legacy_first_draw_then_semantic_target_lock_v1"

TARGET_OK = "ok"
TARGET_AMBIGUOUS = "ambiguous"
TARGET_ABSENT = "absent"

_WS = re.compile(r"\s+")


def normalize_url(url: str) -> str:
    """只移除 fragment；其余（含 query、端口、大小写）保留。"""
    text = str(url or "").strip()
    return text.split("#", 1)[0]


def normalize_text(value: str) -> str:
    return _WS.sub(" ", str(value or "").strip())


def dom_identity(element: dict, page_url: str) -> dict:
    """由一个元素信息构造语义身份。缺失 role/name 时仍可用 href 或返回空身份。"""
    element = element if isinstance(element, dict) else {}
    attrs = element.get("attrs") if isinstance(element.get("attrs"), dict) else {}
    return {
        "kind": "dom",
        "page_url": normalize_url(page_url),
        "role": normalize_text(element.get("role")),
        "name": normalize_text(element.get("name")),
        "href": normalize_url(attrs.get("url")) if attrs.get("url") else None,
        "role_index": element.get("role_index"),
        "policy_version": TARGET_POLICY_VERSION,
    }


def identify_element(elements: dict, element_id, page_url: str) -> dict | None:
    """从当前观测里取某个数字 id 的语义身份；该 id 不存在返回 None。"""
    info = (elements or {}).get(str(element_id))
    if not isinstance(info, dict):
        return None
    return dom_identity(info, page_url)


def param_identity(action: str, element: dict, page_url: str) -> dict:
    """参数动作的目标身份：页面 + 动作类型 + 目标元素语义身份。"""
    identity = dom_identity(element, page_url)
    identity["kind"] = "param"
    identity["action"] = normalize_text(action)
    return identity


def _same_target(identity: dict, element: dict, page_url: str) -> bool:
    if normalize_url(page_url) != identity.get("page_url"):
        return False
    if normalize_text(element.get("role")) != identity.get("role"):
        return False
    if normalize_text(element.get("name")) != identity.get("name"):
        return False
    wanted_href = identity.get("href")
    if wanted_href is not None:
        attrs = element.get("attrs") if isinstance(element.get("attrs"), dict) else {}
        if normalize_url(attrs.get("url")) != wanted_href:
            return False
    return True


def matches_param(identity: dict, action: str, element, page_url: str) -> bool:
    """当前参数动作是否**针对被锁定的目标**：动作类型相同且元素语义身份一致。

    这是"同目标、同类动作"的关键：只在页面上"还能找到原目标"是不够的——
    对别的元素点击、或对原目标做 hover，都不应被改写。
    """
    if not isinstance(element, dict) or identity.get("kind") != "param":
        return False
    if normalize_text(action) != identity.get("action"):
        return False
    return _same_target(identity, element, page_url)


def count_matches(identity: dict, elements: dict, page_url: str) -> int:
    """当前页上有多少元素与给定身份相同（用于识别"同名元素"歧义）。"""
    if not identity or normalize_url(page_url) != identity.get("page_url"):
        return 0
    return sum(1 for info in (elements or {}).values()
               if isinstance(info, dict) and _same_target(identity, info, page_url))


def match_identity(identity: dict, elements: dict, page_url: str) -> tuple[str | None, str]:
    """在当前页重新识别被锁定的目标。

    Returns:
        ``(element_id | None, status)``，status ∈ ``ok`` / ``ambiguous`` / ``absent``。
        ``absent`` 含"换到了别的页面"这种情况（页面 URL 不匹配）。
    """
    if not identity or identity.get("kind") not in ("dom", "param"):
        return None, TARGET_ABSENT
    if normalize_url(page_url) != identity.get("page_url"):
        return None, TARGET_ABSENT
    candidates = [str(eid) for eid, info in (elements or {}).items()
                  if isinstance(info, dict) and _same_target(identity, info, page_url)]
    if not candidates:
        return None, TARGET_ABSENT
    if len(candidates) == 1:
        return candidates[0], TARGET_OK
    # 多个候选：一律 ambiguous。**不用 role_index 消歧**——它只是同页渲染中的
    # 出现次序，刷新/重排后会变，用它挑元素可能屏蔽错元素。
    return None, TARGET_AMBIGUOUS


def describe(identity: dict) -> str:
    if not identity:
        return "<none>"
    if identity.get("kind") == "param":
        return (f"param action={identity.get('action')!r} role={identity.get('role')!r} "
                f"name={identity.get('name')!r} @ {identity.get('page_url')}")
    return (f"dom role={identity.get('role')!r} name={identity.get('name')!r} "
            f"href={identity.get('href')!r} @ {identity.get('page_url')}")
