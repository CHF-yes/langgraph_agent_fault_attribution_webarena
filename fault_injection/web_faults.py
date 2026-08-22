"""
Web 底层故障注入 — 4 种故障类型。

① 超时注入: 动作前 sleep，模拟网络延迟
② HTTP 错误: goto 返回 500/503 假页面
③ DOM 元素丢失: 从 aria_snapshot 中随机删元素
④ 弹窗遮挡: 在 aria_snapshot 顶部插入 alert/dialog
"""

import re
import time
import random as sys_random

from fault_injection.config import FaultConfig


# ============================================================
# ① 超时注入
# ============================================================

def inject_timeout(config: FaultConfig) -> float:
    """
    注入动作前延迟。

    Returns:
        实际延迟秒数（0 表示未注入）
    """
    if not config.should_inject("web_timeout"):
        return 0.0

    delay = config.get_delay("web_timeout")
    time.sleep(delay)

    config.record_injection("web_timeout", {"delay_sec": round(delay, 2)})
    return delay


# ============================================================
# ② HTTP 错误注入
# ============================================================

HTTP_ERROR_PAGES = {
    "500": {
        "url": "about:error",
        "content": "[1] heading '500 Internal Server Error'\n"
                   "  [2] text 'The server encountered an internal error and was unable to complete your request.'\n"
                   "[3] text 'Please try again later or contact the administrator.'\n"
                   "[4] link 'Return to home'",
        "elements": {
            "1": "heading 500 Internal Server Error",
            "2": "text error message",
            "3": "text try again message",
            "4": "link Return to home",
        },
    },
    "503": {
        "url": "about:error",
        "content": "[1] heading '503 Service Unavailable'\n"
                   "  [2] text 'The server is temporarily unable to service your request due to maintenance downtime or capacity problems.'\n"
                   "[3] text 'Please try again later.'\n"
                   "[4] link 'Retry'",
        "elements": {
            "1": "heading 503 Service Unavailable",
            "2": "text error description",
            "3": "text try again message",
            "4": "link Retry",
        },
    },
}


def inject_http_error(config: FaultConfig, obs: dict) -> dict:
    """
    以一定概率将页面观测替换为 500/503 错误页。

    Args:
        config: 故障配置
        obs: 原始观测 {"url": str, "page_content": str, "elements": dict}

    Returns:
        可能被替换的观测 dict
    """
    if not config.should_inject("web_http_error"):
        return obs

    error_type = config.rng.pick(["500", "503"])
    error_page = HTTP_ERROR_PAGES[error_type]

    config.record_injection("web_http_error", {
        "error_type": error_type,
        "original_url": obs.get("url", ""),
    })

    return {
        "url": error_page["url"],
        "page_content": error_page["content"],
        "elements": error_page["elements"],
    }


# ============================================================
# ③ DOM 元素丢失
# ============================================================

def inject_dom_missing(config: FaultConfig, obs: dict) -> dict:
    """
    从 aria_snapshot 中随机删除 1-3 个元素条目。

    低强度删 1 个（非关键），中强度删 2 个，高强度删 3 个。

    Args:
        config: 故障配置
        obs: 原始观测

    Returns:
        删减后的观测
    """
    if not config.should_inject("web_dom_missing"):
        return obs

    content = obs.get("page_content", "")
    elements = obs.get("elements", {})

    if not elements or len(elements) <= 1:
        return obs

    # 按强度决定删除数量
    intensity = config.intensity
    if intensity == "low":
        remove_count = 1
    elif intensity == "medium":
        remove_count = min(2, len(elements) - 1)
    else:  # high
        remove_count = min(3, len(elements) - 1)

    # 随机选择要删除的元素
    all_ids = list(elements.keys())
    to_remove = config.rng.pick(
        _combinations(all_ids, remove_count)
    ) if remove_count == 1 else _sample_multiple(config.rng, all_ids, remove_count)

    if not isinstance(to_remove, list):
        to_remove = [to_remove]

    # 从 element_map 中删除
    new_elements = {k: v for k, v in elements.items() if k not in to_remove}

    # 从 page_content 中删除对应行
    new_content = _remove_lines_by_ids(content, to_remove)

    config.record_injection("web_dom_missing", {
        "removed_ids": to_remove,
        "remove_count": len(to_remove),
    })

    return {
        "url": obs.get("url", ""),
        "page_content": new_content,
        "elements": new_elements,
    }


def _remove_lines_by_ids(content: str, ids_to_remove: list[str]) -> str:
    """从 aria_snapshot 文本中删除指定 [id=xxx] 的行。"""
    lines = content.split("\n")
    filtered = []
    for line in lines:
        # 检查该行是否包含要删除的 id
        should_keep = True
        for eid in ids_to_remove:
            if re.search(rf'\[{eid}\]', line):
                should_keep = False
                break
        if should_keep:
            filtered.append(line)
    return "\n".join(filtered)


def _combinations(items: list, k: int) -> list:
    """生成所有 k-组合。"""
    if k == 0:
        return []
    if k == 1:
        return [[x] for x in items]
    result = []
    for i, item in enumerate(items):
        for rest in _combinations(items[i+1:], k - 1):
            result.append([item] + rest)
    return result


def _sample_multiple(rng, items: list, k: int) -> list:
    """从列表中随机不放回选 k 个。"""
    pool = list(items)
    result = []
    for _ in range(k):
        if not pool:
            break
        idx = rng.randint(0, len(pool) - 1)
        result.append(pool.pop(idx))
    return result


# ============================================================
# ④ 弹窗遮挡
# ============================================================

POPUP_TEMPLATES = [
    {
        "id_prefix": "popup",
        "content_lines": [
            "[popup_1] alertdialog 'Cookie Consent'\n"
            "  [popup_2] text 'This website uses cookies to improve your experience. "
            "By continuing, you agree to our use of cookies.'\n"
            "  [popup_3] button 'Accept All'\n"
            "  [popup_4] button 'Manage Preferences'",
        ],
        "elements": {
            "popup_1": "alertdialog Cookie Consent",
            "popup_2": "text cookie message",
            "popup_3": "button Accept All",
            "popup_4": "button Manage Preferences",
        },
    },
    {
        "id_prefix": "popup",
        "content_lines": [
            "[popup_1] dialog 'Newsletter Signup'\n"
            "  [popup_2] heading 'Subscribe to our newsletter!'\n"
            "  [popup_3] textbox 'Enter your email'\n"
            "  [popup_4] button 'Subscribe'\n"
            "  [popup_5] button 'No thanks'",
        ],
        "elements": {
            "popup_1": "dialog Newsletter Signup",
            "popup_2": "heading Subscribe to newsletter",
            "popup_3": "textbox email input",
            "popup_4": "button Subscribe",
            "popup_5": "button No thanks",
        },
    },
    {
        "id_prefix": "popup",
        "content_lines": [
            "[popup_1] alert 'Session Timeout Warning'\n"
            "  [popup_2] text 'Your session is about to expire due to inactivity. "
            "Click OK to continue.'\n"
            "  [popup_3] button 'OK'\n"
            "  [popup_4] button 'Log Out'",
        ],
        "elements": {
            "popup_1": "alert Session Timeout Warning",
            "popup_2": "text session timeout message",
            "popup_3": "button OK",
            "popup_4": "button Log Out",
        },
    },
]


def inject_popup(config: FaultConfig, obs: dict) -> dict:
    """
    在 aria_snapshot 顶部插入弹窗/对话框。

    Args:
        config: 故障配置
        obs: 原始观测

    Returns:
        带弹窗的观测
    """
    if not config.should_inject("web_popup_block"):
        return obs

    popup = config.rng.pick(POPUP_TEMPLATES)

    # 插入到内容顶部
    popup_content = popup["content_lines"][0]
    new_content = popup_content + "\n" + obs.get("page_content", "")

    # 合并元素映射
    new_elements = dict(popup["elements"])
    new_elements.update(obs.get("elements", {}))

    config.record_injection("web_popup_block", {
        "popup_type": popup["elements"].get("popup_1", "unknown"),
    })

    return {
        "url": obs.get("url", ""),
        "page_content": new_content,
        "elements": new_elements,
    }
