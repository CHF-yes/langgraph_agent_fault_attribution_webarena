"""
WebArena 标准浏览器操作工具。

支持两种模式：
1. 模拟模式（默认）：使用 PageState 内存模拟，无需浏览器
2. 浏览器模式：对接 Playwright BrowserEnv，真实操作网页

工具签名保持不变，模式切换对 agent 透明。
"""

from typing import Optional
from dataclasses import dataclass, field
from langchain_core.tools import tool


# ============================================================
# 模拟页面状态（无浏览器时的 fallback）
# ============================================================

@dataclass
class PageState:
    """模拟浏览器页面状态。"""
    url: str = "about:blank"
    content: str = ""
    history: list[str] = field(default_factory=list)
    forward_stack: list[str] = field(default_factory=list)
    elements: dict[str, str] = field(default_factory=dict)
    input_values: dict[str, str] = field(default_factory=dict)


_page_state = PageState()
_browser_env = None  # SyncBrowserEnv 实例（浏览器模式下注入）


# ============================================================
# 模式管理
# ============================================================

def use_browser(env) -> None:
    """
    切换到浏览器模式。

    Args:
        env: SyncBrowserEnv 实例
    """
    global _browser_env
    _browser_env = env


def use_simulation() -> None:
    """切换到模拟模式。"""
    global _browser_env
    _browser_env = None


def reset_page_state():
    """重置模拟页面状态。"""
    global _page_state
    _page_state = PageState()


def set_page_state(url: str = None, content: str = None,
                   elements: dict = None):
    """外部注入模拟页面状态。"""
    if url is not None:
        _page_state.url = url
    if content is not None:
        _page_state.content = content
    if elements is not None:
        _page_state.elements = elements


def get_page_state() -> PageState:
    return _page_state


def get_current_observation() -> dict:
    """
    获取当前页面观测（浏览器模式返回真实 AX Tree，模拟模式返回假数据）。
    供 agent 节点调用。
    """
    if _browser_env is not None:
        obs = _browser_env.get_obs()
        if obs:
            return {
                "url": obs.url,
                "page_content": obs.ax_tree_text,
                "elements": obs.element_map,
            }
        return {"url": "about:blank", "page_content": "", "elements": {}}
    else:
        return {
            "url": _page_state.url,
            "page_content": _page_state.content,
            "elements": _page_state.elements,
        }


# ============================================================
# 9 个 WebArena 标准工具
# ============================================================

@tool
def click(element_id: str) -> str:
    """
    点击页面上的指定元素。

    Args:
        element_id: 元素编号（来自页面 AX Tree 中的 [id=xxx] 标记）

    Returns:
        操作结果描述 + 新页面内容摘要
    """
    if _browser_env is not None:
        # ---- 浏览器模式 ----
        try:
            obs = _browser_env.click(element_id)
            return (
                f"[OK] 已点击元素 [{element_id}]，页面已更新。\n"
                f"当前 URL: {obs.url}\n"
                f"新页面内容:\n{obs.ax_tree_text[:6000]}"
            )
        except Exception as e:
            return f"[Error] 点击失败: {e}"

    # ---- 模拟模式 ----
    elem_text = _page_state.elements.get(str(element_id))
    if elem_text is None:
        return f"[Error] 未找到元素 element_id={element_id}"
    return f"[OK] 已点击元素 [{element_id}]「{elem_text}」"


@tool
def type_text(element_id: str, text: str) -> str:
    """
    在输入框中输入文本。使用前请先 click 该元素使其获得焦点。

    Args:
        element_id: 输入框元素编号
        text: 要输入的文本

    Returns:
        操作结果 + 新页面内容摘要
    """
    if _browser_env is not None:
        try:
            obs = _browser_env.type_text(element_id, text)
            return (
                f"[OK] 已在元素 [{element_id}] 中输入: {text}\n"
                f"当前 URL: {obs.url}\n"
                f"页面内容:\n{obs.ax_tree_text[:6000]}"
            )
        except Exception as e:
            return f"[Error] 输入失败: {e}"

    elem_text = _page_state.elements.get(str(element_id))
    if elem_text is None:
        return f"[Error] 未找到元素 element_id={element_id}"
    _page_state.input_values[str(element_id)] = text
    return f"[OK] 已在元素 [{element_id}]「{elem_text}」中输入: {text}"


@tool
def scroll(direction: str) -> str:
    """
    向上或向下滚动页面以显示更多内容。

    Args:
        direction: "up" 或 "down"

    Returns:
        操作结果 + 新页面内容摘要
    """
    if direction not in ("up", "down"):
        return f"[Error] scroll 方向必须是 'up' 或 'down'，收到了 '{direction}'"

    if _browser_env is not None:
        try:
            obs = _browser_env.scroll(direction)
            return (
                f"[OK] 页面已向{direction}滚动。\n"
                f"当前 URL: {obs.url}\n"
                f"新页面内容:\n{obs.ax_tree_text[:6000]}"
            )
        except Exception as e:
            return f"[Error] 滚动失败: {e}"

    return f"[OK] 页面已向{direction}滚动。可能显示新的元素。"


@tool
def goto(url: str) -> str:
    """
    导航到指定 URL。

    Args:
        url: 目标 URL

    Returns:
        操作结果 + 新页面内容摘要
    """
    if _browser_env is not None:
        try:
            obs = _browser_env.goto(url)
            return (
                f"[OK] 已导航到 {url}\n"
                f"页面内容:\n{obs.ax_tree_text[:6000]}"
            )
        except Exception as e:
            return f"[Error] 导航失败: {e}"

    _page_state.history.append(_page_state.url)
    _page_state.forward_stack.clear()
    _page_state.url = url
    _page_state.input_values.clear()
    return f"[OK] 已导航到 {url}"


@tool
def go_back() -> str:
    """
    返回上一页（浏览器后退按钮）。

    Returns:
        操作结果 + 新页面内容摘要
    """
    if _browser_env is not None:
        try:
            obs = _browser_env.go_back()
            return (
                f"[OK] 已返回到 {obs.url}\n"
                f"页面内容:\n{obs.ax_tree_text[:6000]}"
            )
        except Exception as e:
            return f"[Error] 返回失败: {e}"

    if not _page_state.history:
        return "[Error] 没有上一页，无法后退。"
    _page_state.forward_stack.append(_page_state.url)
    _page_state.url = _page_state.history.pop()
    _page_state.input_values.clear()
    return f"[OK] 已返回到 {_page_state.url}"


@tool
def go_forward() -> str:
    """
    前进到下一页（浏览器前进按钮）。

    Returns:
        操作结果 + 新页面内容摘要
    """
    if _browser_env is not None:
        try:
            obs = _browser_env.go_forward()
            return (
                f"[OK] 已前进到 {obs.url}\n"
                f"页面内容:\n{obs.ax_tree_text[:6000]}"
            )
        except Exception as e:
            return f"[Error] 前进失败: {e}"

    if not _page_state.forward_stack:
        return "[Error] 没有下一页，无法前进。"
    _page_state.history.append(_page_state.url)
    _page_state.url = _page_state.forward_stack.pop()
    _page_state.input_values.clear()
    return f"[OK] 已前进到 {_page_state.url}"


@tool
def stop(answer: str) -> str:
    """
    完成任务并返回最终答案。信息已收集完毕、任务已完成时调用。

    Args:
        answer: 任务的最终答案

    Returns:
        确认消息
    """
    return f"[OK] 任务完成！答案: {answer}"


@tool
def select_option(element_id: str, option: str) -> str:
    """
    在下拉选择框中选择一个选项。

    Args:
        element_id: 下拉框元素编号
        option: 要选择的选项文本

    Returns:
        操作结果 + 新页面内容摘要
    """
    if _browser_env is not None:
        try:
            obs = _browser_env.select_option(element_id, option)
            return (
                f"[OK] 已在元素 [{element_id}] 中选择: {option}\n"
                f"当前 URL: {obs.url}\n"
                f"页面内容:\n{obs.ax_tree_text[:6000]}"
            )
        except Exception as e:
            return f"[Error] 选择失败: {e}"

    elem_text = _page_state.elements.get(str(element_id))
    if elem_text is None:
        return f"[Error] 未找到元素 element_id={element_id}"
    _page_state.input_values[str(element_id)] = option
    return f"[OK] 已在元素 [{element_id}]「{elem_text}」中选择: {option}"


@tool
def hover(element_id: str) -> str:
    """
    将鼠标悬停在指定元素上（用于显示 tooltip、下拉菜单等）。

    Args:
        element_id: 目标元素编号

    Returns:
        操作结果 + 新页面内容摘要
    """
    if _browser_env is not None:
        try:
            obs = _browser_env.hover(element_id)
            return (
                f"[OK] 鼠标已悬停在元素 [{element_id}] 上\n"
                f"页面内容:\n{obs.ax_tree_text[:6000]}"
            )
        except Exception as e:
            return f"[Error] 悬停失败: {e}"

    elem_text = _page_state.elements.get(str(element_id))
    if elem_text is None:
        return f"[Error] 未找到元素 element_id={element_id}"
    return f"[OK] 鼠标已悬停在元素 [{element_id}]「{elem_text}」上"


# ============================================================
# 工具列表
# ============================================================

ALL_TOOLS = [
    click,
    type_text,
    scroll,
    goto,
    go_back,
    go_forward,
    stop,
    select_option,
    hover,
]
