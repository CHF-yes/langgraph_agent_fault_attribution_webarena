"""
Agent 层故障注入 — 模拟 agent 自身状态误判与参数错误。

与 Web/GitLab 环境故障不同，这类故障作用于 agent 的输入/动作参数，
用于测试 agent 在"自己犯错"或"观测被误导"时的鲁棒性：

① 状态误判: 在观测中随机篡改元素名称，诱导 agent 对页面状态产生错误判断
② 参数错误: 随机将工具调用参数改为无效值（不存在的元素 id、污染文本）

可复现性同样由 FaultConfig.seed 保证。
"""

from fault_injection.config import FaultConfig

# 模块级状态：记录连续状态误判次数（reset 时清零）
_MISJUDGE_COUNT = 0


# ============================================================
# ① 状态误判
# ============================================================

def inject_state_misjudge(config: FaultConfig, obs: dict) -> dict:
    """
    以一定概率篡改观测中的元素名称，模拟 agent 状态误判。

    实现：随机选择一个元素，将它的 name 替换为误导性文本（保留 role 和 id），
    使 agent 看到"错误的状态"。

    Args:
        config: 故障配置
        obs: 原始观测 {"url": str, "page_content": str, "elements": dict}

    Returns:
        可能被篡改的观测 dict
    """
    global _MISJUDGE_COUNT

    if not config.should_inject("agent_state_misjudge"):
        return obs

    elements = obs.get("elements", {})
    if not elements:
        return obs

    target_id = config.rng.pick(list(elements.keys()))
    info = elements.get(str(target_id))

    if not isinstance(info, dict):
        return obs

    misjudged_text = "（状态可能已变化，请重新确认）"
    old_name = info.get("name", "")
    new_name = f"{old_name} {misjudged_text}" if old_name else misjudged_text

    new_info = dict(info)
    new_info["name"] = new_name

    new_elements = dict(elements)
    new_elements[str(target_id)] = new_info

    # 同步替换 page_content 中对应的行
    content = obs.get("page_content", "")
    new_content = _replace_element_name(content, str(target_id), new_name)

    _MISJUDGE_COUNT += 1

    config.record_injection("agent_state_misjudge", {
        "element_id": str(target_id),
        "old_name": old_name,
        "new_name": new_name,
        "count": _MISJUDGE_COUNT,
    })

    return {
        "url": obs.get("url", ""),
        "page_content": new_content,
        "elements": new_elements,
    }


def _replace_element_name(content: str, element_id: str, new_name: str) -> str:
    """在 aria_snapshot 文本中把 [id=x] 行里的元素名替换为新名称。"""
    lines = content.split("\n")
    result = []
    for line in lines:
        if f"[{element_id}]" in line:
            # 行格式: "[id] role 'name' ..." — 替换引号内内容（简单处理）
            import re
            if "'" in line:
                line = re.sub(r"'(.*?)'", f"'{new_name}'", line, count=1)
            else:
                line = f"{line} {new_name}"
        result.append(line)
    return "\n".join(result)


# ============================================================
# ② 参数错误
# ============================================================

def inject_param_error(config: FaultConfig, action: str,
                       element_id: str = None, text: str = None):
    """
    以一定概率将工具调用参数篡改为错误值。

    Args:
        config: 故障配置
        action: 工具名（如 "click", "type_text"）
        element_id: 原始元素 id（可为 None）
        text: 原始文本（仅 type_text 使用，可为 None）

    Returns:
        (element_id, text) 可能被篡改后的参数
    """
    if not config.should_inject_parameter_action("agent_param_error"):
        return element_id, text

    detail = {"action": action}

    # 篡改 element_id：替换为不存在的 id（但保留格式）
    if element_id is not None:
        original_eid = str(element_id)
        # 使用 seed 可控的随机无效 id
        element_id = f"invalid_{config.rng.randint(1000, 9999)}"
        detail["original_element_id"] = original_eid
        detail["new_element_id"] = element_id

    # 篡改 text：追加干扰文本
    if text is not None:
        original_text = str(text)
        text = f"{original_text} [输入确认失败，请重试]"
        detail["original_text"] = original_text
        detail["new_text"] = text

    config.record_injection("agent_param_error", detail)
    return element_id, text


# ============================================================
# 状态重置
# ============================================================

def reset_state_misjudge():
    """重置模块级状态（新任务开始时调用）。"""
    global _MISJUDGE_COUNT
    _MISJUDGE_COUNT = 0
