"""
BrowserEnv - Playwright 浏览器环境封装。

负责：
1. 启动/管理 Playwright 浏览器实例
2. 将工具调用翻译为真实浏览器操作
3. 提取页面可访问性树 (AX Tree) 作为 agent 观测
4. 维护 element_id → DOM 元素的映射
"""

import asyncio
import re
import threading
from typing import Optional
from dataclasses import dataclass, field

try:
    from playwright.async_api import async_playwright, Page, Browser
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


# ============================================================
# aria_snapshot 解析器（新版 Playwright API）
# ============================================================

def parse_aria_snapshot(snapshot_text: str) -> tuple[str, dict]:
    """
    解析 Playwright page.aria_snapshot() 的输出，生成带 ID 的文本 + 元素映射。

    aria_snapshot 输出格式（类似 YAML）:
        - heading "Page Title" [level=1]
        - link "Home"
          - text "sub element"
        - textbox "Search" [active]

    Args:
        snapshot_text: aria_snapshot() 的原始输出

    Returns:
        (formatted_text, element_map)
        formatted_text: 带 [id=xxx] 标记的文本
        element_map: {eid: {"role": str, "name": str, "attrs": dict}}
    """
    if not snapshot_text:
        return "(empty page)", {}

    lines = snapshot_text.split("\n")
    formatted_lines = []
    element_map = {}
    combo_counts: dict[tuple, int] = {}
    counter = 0

    for line in lines:
        if not line.strip():
            continue

        # 解析缩进深度
        stripped = line.lstrip()
        indent = (len(line) - len(stripped)) // 2

        # Playwright emits URL metadata as a child line. It is an attribute of
        # the preceding link, not an interactive element of its own.
        if stripped.startswith("- /url ") or stripped.startswith("- /url:"):
            if element_map:
                match = re.search(r"- /url:?\s+['\"]?(.*?)['\"]?$", stripped)
                if match:
                    last_id = str(counter)
                    element_map[last_id].setdefault("attrs", {})["url"] = match.group(1)
            continue

        # 解析行格式: "- role "name" [attrs]" 或 "- text"
        parsed = _parse_aria_line(stripped)
        if parsed is None:
            continue

        role, name, attrs = parsed

        # 跳过纯容器节点
        if role in ("none", "generic", "paragraph", "group") and not name:
            # 仍输出缩进以保持结构（不分配 ID）
            continue

        counter += 1
        eid = str(counter)
        indent_str = "  " * indent
        name_str = f" '{name}'" if name else ""

        # 记录同一 (role, name) 组合的出现序号，便于精确定位（Playwright nth）
        # 注意：nth 的索引必须基于 get_by_role(role, name=...) 的过滤结果，
        # 即同一 (role, name) 内的序号，而不是该 role 的全局序号
        combo_counts[(role, name)] = combo_counts.get((role, name), 0) + 1
        role_index = combo_counts[(role, name)] - 1

        formatted_lines.append(f"{indent_str}[{eid}] {role}{name_str}")
        element_map[eid] = {
            "role": role,
            "name": name,
            "attrs": attrs,
            "role_index": role_index,
        }

    return "\n".join(formatted_lines), element_map


def _parse_aria_line(line: str) -> tuple:
    """
    解析 aria_snapshot 的单行。

    输入格式:
        '- heading "Page Title" [level=1]'
        '- link "Home"'
        '- textbox "Search" [active]'
        '- paragraph: long text content here'
        '- text'
        '- button "Click me" [disabled]'
        '- button: 搜索 "Search" [name="Search"i]'   # Magento 等站点实际格式

    返回: (role, name, attrs_dict) 或 None
    """
    line = line.strip()

    # 移除开头的 "- "
    if line.startswith("- "):
        line = line[2:]
    elif line.startswith("-"):
        line = line[1:]
    else:
        return None

    # Playwright may quote a complete role/name expression, for example:
    # `'button "Sort by: Submissions":'`.
    quoted_role = re.match(r"^['\"]([a-zA-Z_-]+)\s+(['\"].*?['\"])['\"]:?$", line)
    if quoted_role:
        line = f"{quoted_role.group(1)} {quoted_role.group(2)}"

    # 提取属性 [key=value]
    attrs = {}
    attr_match = re.findall(r'\[([^\]]+)\]', line)
    if attr_match:
        for attr_str in attr_match:
            if "=" in attr_str:
                k, v = attr_str.split("=", 1)
                attrs[k.strip()] = v.strip()
            else:
                attrs[attr_str.strip()] = True
        line = re.sub(r'\s*\[[^\]]*\]', '', line)

    # 从 attrs["name"] 提取可访问名（如 name="Search"i → "Search"）
    attr_name = ""
    if isinstance(attrs.get("name"), str) and attrs["name"].strip():
        raw = attrs["name"].strip()
        # 支持 "Search" / 'Search' / Search 及大小写修饰符（如 ...i）
        m = re.match(r'^(["\'])(.*?)\1', raw)
        if m:
            attr_name = m.group(2)
        else:
            attr_name = re.sub(r'i$', '', raw).strip()
        attrs["name"] = attr_name

    # 提取引号内的 name（标准格式: role "name" 或 role 'name'）
    quoted_name = ""
    name_match = re.search(r'["\']([^"\']*)["\']', line)
    if name_match:
        quoted_name = name_match.group(1)
        line = re.sub(r'\s*["\'][^"\']*["\']', '', line)

    # 处理 "role: name" 格式（Magento 等站点会输出 button: 搜索 'Search'）
    role = ""
    colon_name = ""
    if ":" in line:
        parts = line.split(":", 1)
        role = parts[0].strip()
        colon_name = parts[1].strip()
        # 移除残留的引号（单引号包裹的名称片段）
        colon_name = re.sub(r"^'|'$", "", colon_name).strip()
    else:
        role = line.strip()

    # In `button: Sort by 'button'`, the quoted suffix is the role hint, not
    # the accessible name. Magento-style `button: 搜索 "Search"` still uses
    # the quoted value as the name.
    if colon_name and quoted_name.casefold() == role.casefold():
        quoted_name = ""

    # 最终 name 优先级：attrs name > 双引号 name > 冒号后 name
    name = attr_name or quoted_name or colon_name
    name = name.strip().strip("\"'")

    if not role:
        return None

    return role, name, attrs


async def wait_for_page_stable(page: "Page", timeout_ms: int = 5000):
    """等待页面网络空闲 + 额外延迟。"""
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass
    await page.wait_for_timeout(500)


# ============================================================
# BrowserEnv 主类
# ============================================================

@dataclass
class PageObservation:
    """一次页面观测结果"""
    url: str
    ax_tree_text: str                      # 展平的可访问性树文本
    element_map: dict[str, dict]           # eid → {uid, role, name}
    raw_ax: Optional[str] = None          # 原始 aria_snapshot 文本（调试用）


class BrowserEnv:
    """
    Playwright 浏览器环境。

    用法:
        env = BrowserEnv(headless=False)
        await env.start()
        obs = await env.goto("https://shop.example.com")
        obs = await env.click("5")         # 点击 id=5 的元素
        obs = await env.type_text("3", "hello")
        ...
        await env.stop()
    """

    def __init__(self, headless: bool = True, slow_mo: int = 0,
                 viewport_width: int = 1280, viewport_height: int = 720,
                 extra_http_headers: dict | None = None,
                 har_path: str | None = None,
                 storage_state: str | None = None):
        self.headless = headless
        self.slow_mo = slow_mo            # 操作间延迟（ms），便于观察
        self.viewport = {"width": viewport_width, "height": viewport_height}
        self._extra_http_headers = extra_http_headers or {}
        self._har_path = har_path
        self._storage_state = storage_state
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context = None
        self._page: Optional[Page] = None
        self._current_obs: Optional[PageObservation] = None
        self.fallback_events: list[dict] = []

    # ---- 生命周期 ----

    async def start(self):
        """启动浏览器。"""
        if not HAS_PLAYWRIGHT:
            raise RuntimeError(
                "Playwright 未安装。请运行: pip install playwright && playwright install chromium"
            )
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo,
        )
        context_options = {
            "viewport": self.viewport,
            "extra_http_headers": self._extra_http_headers or None,
        }
        if self._har_path:
            context_options["record_har_path"] = self._har_path
        if self._storage_state:
            context_options["storage_state"] = self._storage_state
        self._context = await self._browser.new_context(**context_options)
        self._page = await self._context.new_page()
        # 导航到空白页初始化
        await self._page.goto("about:blank")
        self._current_obs = await self._observe()

    async def stop(self):
        """关闭浏览器。"""
        # Playwright writes HAR data when the BrowserContext closes, before the
        # browser itself is closed.
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    # ---- 页面观测 ----

    async def _observe(self) -> PageObservation:
        """提取当前页面的 ARIA 快照作为 agent 观测。"""
        if not self._page:
            return PageObservation(url="about:blank", ax_tree_text="", element_map={})

        url = self._page.url

        # 使用新版 Playwright aria_snapshot API
        try:
            snapshot_text = await self._page.aria_snapshot()
        except Exception:
            snapshot_text = ""

        if snapshot_text:
            ax_tree_text, element_map = parse_aria_snapshot(snapshot_text)
        else:
            ax_tree_text = "(empty page - no accessible elements)"
            element_map = {}

        obs = PageObservation(
            url=url,
            ax_tree_text=ax_tree_text,
            element_map=element_map,
            raw_ax=snapshot_text,
        )
        self._current_obs = obs
        return obs

    def get_current_observation(self) -> Optional[PageObservation]:
        """获取最近一次页面观测。"""
        return self._current_obs

    # ---- 浏览器操作 ----

    async def goto(self, url: str) -> PageObservation:
        """导航到指定 URL。"""
        if not self._page:
            raise RuntimeError("Browser not started")
        await self._page.goto(url, wait_until="domcontentloaded")
        await wait_for_page_stable(self._page)
        return await self._observe()

    async def click(self, element_id: str) -> PageObservation:
        """
        点击指定元素。

        Args:
            element_id: AX Tree 中的元素编号 [id=xxx]

        Returns:
            操作后的新页面观测
        """
        elem_info = self._get_element(element_id)
        if not elem_info:
            raise ValueError(f"Element with id={element_id} not found in current page")

        role = elem_info["role"]
        name = elem_info["name"]
        role_index = elem_info.get("role_index", 0)
        element_url = elem_info.get("attrs", {}).get("url")

        try:
            locator = self._locate_element(
                "", role, name, role_index, element_url=element_url
            )
            await locator.click(timeout=5000)
        except Exception as e:
            if element_url and role == "link":
                from urllib.parse import urljoin

                self.fallback_events.append({
                    "fallback_used": True,
                    "requested_action": "click",
                    "executed_action": "goto",
                    "element_id": str(element_id),
                    "role": role,
                    "name": name,
                    "url": element_url,
                    "reason": type(e).__name__,
                })
                await self._page.goto(urljoin(self._page.url, element_url),
                                       wait_until="domcontentloaded")
                await wait_for_page_stable(self._page)
                return await self._observe()
            raise RuntimeError(
                f"Failed to click element [{element_id}] ({role} '{name}'): {e}"
            )

        await wait_for_page_stable(self._page)
        return await self._observe()

    async def type_text(self, element_id: str, text: str) -> PageObservation:
        """在输入框中输入文本。"""
        elem_info = self._get_element(element_id)
        if not elem_info:
            raise ValueError(f"Element with id={element_id} not found")

        locator = self._locate_element(
            "", elem_info["role"], elem_info["name"],
            role_index=elem_info.get("role_index", 0),
        )
        await locator.click(timeout=3000)
        await locator.fill(text, timeout=5000)

        await wait_for_page_stable(self._page)
        return await self._observe()

    async def scroll(self, direction: str) -> PageObservation:
        """滚动页面。"""
        if direction == "down":
            await self._page.evaluate("window.scrollBy(0, window.innerHeight)")
        elif direction == "up":
            await self._page.evaluate("window.scrollBy(0, -window.innerHeight)")
        else:
            raise ValueError(f"Invalid scroll direction: {direction}")

        await self._page.wait_for_timeout(500)
        return await self._observe()

    async def go_back(self) -> PageObservation:
        """浏览器后退。"""
        await self._page.go_back()
        await wait_for_page_stable(self._page)
        return await self._observe()

    async def go_forward(self) -> PageObservation:
        """浏览器前进。"""
        await self._page.go_forward()
        await wait_for_page_stable(self._page)
        return await self._observe()

    async def select_option(self, element_id: str, option: str) -> PageObservation:
        """下拉选择。"""
        elem_info = self._get_element(element_id)
        if not elem_info:
            raise ValueError(f"Element with id={element_id} not found")

        locator = self._locate_element(
            "", elem_info["role"], elem_info["name"],
            role_index=elem_info.get("role_index", 0),
        )
        await locator.select_option(label=option, timeout=5000)

        await wait_for_page_stable(self._page)
        return await self._observe()

    async def hover(self, element_id: str) -> PageObservation:
        """鼠标悬停。"""
        elem_info = self._get_element(element_id)
        if not elem_info:
            raise ValueError(f"Element with id={element_id} not found")

        locator = self._locate_element(
            "", elem_info["role"], elem_info["name"],
            role_index=elem_info.get("role_index", 0),
        )
        await locator.hover(timeout=5000)

        await self._page.wait_for_timeout(500)
        return await self._observe()

    # ---- 内部辅助 ----

    def _get_element(self, element_id: str) -> Optional[dict]:
        """从当前观测的元素映射中获取元素信息。"""
        if not self._current_obs:
            return None
        return self._current_obs.element_map.get(str(element_id))

    def _locate_element(self, uid: str, role: str, name: str, role_index: int = 0,
                        element_url: str | None = None):
        """
        通过 role + name 定位 Playwright Locator。

        aria_snapshot 中的每个元素由 (role, name) 唯一标识。
        使用 Playwright 的 get_by_role + get_by_text 组合定位；
        当同一 role+name 存在多个匹配时，使用 role_index 精确定位到
        aria_snapshot 中对应的那一个，而不是永远取 first。

        Args:
            uid: 保留参数（兼容性，当前未使用）
            role: ARIA role（如 "link", "button", "textbox"）
            name: ARIA name（如 "Home", "Search"）
            role_index: 该 role 在 aria_snapshot 中的出现序号（0-based）
        """
        if not self._page:
            raise RuntimeError("Browser not started")

        if element_url and role == "link":
            try:
                # `has=` matches descendants, not the anchor itself. Use an
                # href attribute selector so valid links do not time out and
                # incorrectly enter the fallback path.
                escaped_url = element_url.replace('\\', '\\\\').replace('"', '\\"')
                loc = self._page.locator(f'a[href="{escaped_url}"]')
                return loc.nth(role_index)
            except Exception:
                pass

        # 策略 1: get_by_role + name（最精确）
        if role and name:
            try:
                loc = self._page.get_by_role(role, name=name, exact=False)
                # async API 中 count() 是协程，不能同步调用；
                # nth(role_index) 是同步方法，对单个/多个匹配都适用，
                # 且能精确定位到 aria_snapshot 中对应的那一个
                return loc.nth(role_index)
            except Exception:
                pass

        # 策略 2: 仅按文本匹配（多个匹配时同样按 role_index）
        if name:
            try:
                loc = self._page.get_by_text(name, exact=False)
                return loc.nth(role_index)
            except Exception:
                pass

        # 策略 3: 仅按 role
        if role:
            try:
                loc = self._page.get_by_role(role)
                return loc.nth(role_index)
            except Exception:
                pass

        raise ValueError(
            f"Unable to locate element role={role!r}, name={name!r}, "
            f"role_index={role_index}"
        )


# ============================================================
# 同步封装（供 LangGraph 节点调用）
# ============================================================

class SyncBrowserEnv:
    """
    BrowserEnv 的同步封装。

    LangGraph 节点是同步函数，不能直接 await。此类在内部维护
    一个 event loop，将异步浏览器操作转为同步调用。

    注意：LangGraph 的 ToolNode 可能并行执行多个 tool_call，
    因此所有浏览器操作必须通过锁串行化，否则会触发
    "This event loop is already running"。
    """

    def __init__(self, headless: bool = True, slow_mo: int = 0,
                 extra_http_headers: dict | None = None,
                 har_path: str | None = None,
                 storage_state: str | None = None):
        self._env = BrowserEnv(headless=headless, slow_mo=slow_mo,
                               extra_http_headers=extra_http_headers,
                               har_path=har_path, storage_state=storage_state)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = threading.Lock()

    def _run(self, coro):
        """在事件循环中串行执行异步操作。"""
        with self._lock:
            return self._loop.run_until_complete(coro)

    def start(self):
        """启动浏览器（同步）。"""
        with self._lock:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._env.start())

    def stop(self):
        """关闭浏览器（同步）。"""
        with self._lock:
            if self._loop and self._env:
                self._loop.run_until_complete(self._env.stop())
                self._loop.close()

    def observe(self) -> PageObservation:
        """获取当前页面观测。"""
        return self._run(self._env._observe())

    def get_obs(self) -> Optional[PageObservation]:
        """获取最近观测。"""
        return self._env.get_current_observation()

    @property
    def fallback_events(self) -> list[dict]:
        return self._env.fallback_events

    def goto(self, url: str) -> PageObservation:
        return self._run(self._env.goto(url))

    def click(self, element_id: str) -> PageObservation:
        return self._run(self._env.click(element_id))

    def type_text(self, element_id: str, text: str) -> PageObservation:
        return self._run(self._env.type_text(element_id, text))

    def scroll(self, direction: str) -> PageObservation:
        return self._run(self._env.scroll(direction))

    def go_back(self) -> PageObservation:
        return self._run(self._env.go_back())

    def go_forward(self) -> PageObservation:
        return self._run(self._env.go_forward())

    def select_option(self, element_id: str, option: str) -> PageObservation:
        return self._run(self._env.select_option(element_id, option))

    def hover(self, element_id: str) -> PageObservation:
        return self._run(self._env.hover(element_id))
