"""
GitLab 业务故障注入 — 4 种 GitLab 专属故障。

触发条件均基于上下文特征匹配（url 路径、element 名称、操作类型）。

① CI Runner 离线: 操作 pipeline/CI 相关元素时返回离线提示
② 权限不足: 访问 settings/admin/members 时返回 403
③ 合并冲突: merge 操作返回冲突提示
④ 配额耗尽: 创建类操作返回 quota exceeded
"""

from fault_injection.config import FaultConfig


# ============================================================
# ① CI Runner 离线
# ============================================================

# 触发关键词：element name 或 page content 中匹配
CI_TRIGGER_KEYWORDS = [
    "pipeline", "Pipelines", "CI/CD", "CI / CD",
    "runner", "Runner", "job", "Job", "Jobs",
    "build", "Build", "deploy", "Deploy",
]


def inject_ci_offline(config: FaultConfig, action: str,
                      obs: dict, elem_name: str = "") -> dict:
    """
    CI Runner 离线故障。

    触发条件: 操作的元素名或页面内容包含 CI 关键词
    效果: 将观测替换为 CI Runner 离线错误页面

    Args:
        config: 故障配置
        action: 当前动作名 (click, goto, type_text...)
        obs: 操作后的页面观测
        elem_name: 被操作元素的 ARIA name

    Returns:
        可能被替换的观测
    """
    if not config.should_inject("gitlab_ci_offline"):
        return obs

    # 检查触发条件
    triggered = False

    # 元素名匹配
    if elem_name:
        for kw in CI_TRIGGER_KEYWORDS:
            if kw.lower() in elem_name.lower():
                triggered = True
                break

    # 页面内容匹配
    if not triggered:
        page_content = obs.get("page_content", "")
        for kw in CI_TRIGGER_KEYWORDS:
            if kw.lower() in page_content.lower():
                triggered = True
                break

    if not triggered:
        return obs

    config.record_injection("gitlab_ci_offline", {
        "action": action,
        "elem_name": elem_name,
    })

    return _ci_offline_page()


def _ci_offline_page() -> dict:
    return {
        "url": "about:ci-offline",
        "page_content": (
            "[1] heading 'Pipeline Status'\n"
            "  [2] text 'This job is stuck because no active runners "
            "are available to run it.'\n"
            "[3] alert 'CI Runner Offline'\n"
            "  [4] text 'All configured runners are currently offline. "
            "Please contact your administrator or try again later.'\n"
            "[5] button 'Retry'\n"
            "[6] button 'Cancel Pipeline'"
        ),
        "elements": {
            "1": "heading Pipeline Status",
            "2": "text stuck job message",
            "3": "alert CI Runner Offline",
            "4": "text runner offline description",
            "5": "button Retry",
            "6": "button Cancel Pipeline",
        },
    }


# ============================================================
# ② 权限不足 (403)
# ============================================================

PERMISSION_TRIGGER_PATHS = [
    "settings", "admin", "members",
    "permissions", "access", "billing",
]

PERMISSION_TRIGGER_ELEMENTS = [
    "Settings", "Admin", "Members", "Manage",
    "Permissions", "Access", "Billing",
]


def inject_permission_denied(config: FaultConfig, action: str,
                             obs: dict, url: str = "",
                             elem_name: str = "") -> dict:
    """
    权限不足 403 故障。

    触发条件: URL 路径或元素名包含管理/设置关键词
    效果: 观测替换为 403 禁止访问页面

    Args:
        config: 故障配置
        action: 当前动作
        obs: 操作后的观测
        url: 当前页面 URL
        elem_name: 被操作元素名

    Returns:
        可能被替换的观测
    """
    if not config.should_inject("gitlab_permission"):
        return obs

    triggered = False

    # URL 路径匹配
    if url:
        url_lower = url.lower()
        for path in PERMISSION_TRIGGER_PATHS:
            if path in url_lower:
                triggered = True
                break

    # 元素名匹配
    if not triggered and elem_name:
        for kw in PERMISSION_TRIGGER_ELEMENTS:
            if kw.lower() in elem_name.lower():
                triggered = True
                break

    if not triggered:
        return obs

    config.record_injection("gitlab_permission", {
        "action": action,
        "url": url,
        "elem_name": elem_name,
    })

    return _permission_denied_page()


def _permission_denied_page() -> dict:
    return {
        "url": "about:forbidden",
        "page_content": (
            "[1] heading '403 Forbidden'\n"
            "  [2] text 'You do not have permission to access this resource.'\n"
            "[3] alert 'Access Denied'\n"
            "  [4] text 'This action requires Owner or Maintainer role. "
            "Please contact the project administrator to request access.'\n"
            "[5] link 'Go back to project'\n"
            "[6] link 'Request Access'"
        ),
        "elements": {
            "1": "heading 403 Forbidden",
            "2": "text no permission message",
            "3": "alert Access Denied",
            "4": "text role requirement message",
            "5": "link Go back to project",
            "6": "link Request Access",
        },
    }


# ============================================================
# ③ 合并冲突
# ============================================================

MERGE_TRIGGER_KEYWORDS = [
    "merge", "Merge", "Merge Request",
    "accept merge", "Accept Merge",
]


def inject_merge_conflict(config: FaultConfig, action: str,
                          obs: dict, elem_name: str = "") -> dict:
    """
    合并冲突故障。

    触发条件: 操作的 element name 包含 merge 关键词
    效果: 观测替换为合并冲突页面

    Args:
        config: 故障配置
        action: 当前动作
        obs: 操作后的观测
        elem_name: 被操作元素名

    Returns:
        可能被替换的观测
    """
    if not config.should_inject("gitlab_conflict"):
        return obs

    triggered = False
    if elem_name:
        for kw in MERGE_TRIGGER_KEYWORDS:
            if kw.lower() in elem_name.lower():
                triggered = True
                break

    if not triggered:
        return obs

    config.record_injection("gitlab_conflict", {
        "action": action,
        "elem_name": elem_name,
    })

    return _merge_conflict_page()


def _merge_conflict_page() -> dict:
    return {
        "url": "about:merge-conflict",
        "page_content": (
            "[1] heading 'Merge Conflict Detected'\n"
            "  [2] text 'This merge request cannot be merged automatically "
            "due to conflicts with the target branch.'\n"
            "[3] alert 'Conflicts exist'\n"
            "  [4] text 'The following files have conflicts:'\n"
            "  [5] text '  • src/main.py (3 conflicts)'\n"
            "  [6] text '  • config/settings.yml (1 conflict)'\n"
            "[7] button 'Resolve conflicts locally'\n"
            "[8] button 'Resolve in Web IDE'\n"
            "[9] button 'Close Merge Request'"
        ),
        "elements": {
            "1": "heading Merge Conflict Detected",
            "2": "text merge conflict description",
            "3": "alert Conflicts exist",
            "4": "text conflict file list header",
            "5": "text conflict file main.py",
            "6": "text conflict file settings.yml",
            "7": "button Resolve conflicts locally",
            "8": "button Resolve in Web IDE",
            "9": "button Close Merge Request",
        },
    }


# ============================================================
# ④ 配额耗尽
# ============================================================

QUOTA_TRIGGER_PATHS = [
    "create", "new", "upload", "import",
]

QUOTA_TRIGGER_ELEMENTS = [
    "New project", "Create", "New file", "Upload",
    "New repository", "Add", "Import",
]


def inject_quota_exceeded(config: FaultConfig, action: str,
                          obs: dict, url: str = "",
                          elem_name: str = "") -> dict:
    """
    配额耗尽故障。

    触发条件: URL 包含 create/new 或元素名包含创建关键词
    效果: 观测替换为配额耗尽页面

    Args:
        config: 故障配置
        action: 当前动作
        obs: 操作后的观测
        url: 当前页面 URL
        elem_name: 被操作元素名

    Returns:
        可能被替换的观测
    """
    if not config.should_inject("gitlab_quota"):
        return obs

    triggered = False

    if url:
        url_lower = url.lower()
        for path in QUOTA_TRIGGER_PATHS:
            if path in url_lower:
                triggered = True
                break

    if not triggered and elem_name:
        for kw in QUOTA_TRIGGER_ELEMENTS:
            if kw.lower() in elem_name.lower():
                triggered = True
                break

    if not triggered:
        return obs

    config.record_injection("gitlab_quota", {
        "action": action,
        "url": url,
        "elem_name": elem_name,
    })

    return _quota_exceeded_page()


def _quota_exceeded_page() -> dict:
    return {
        "url": "about:quota-exceeded",
        "page_content": (
            "[1] heading 'Quota Exceeded'\n"
            "  [2] text 'You have reached the limit for this resource.'\n"
            "[3] alert 'Storage Limit Reached'\n"
            "  [4] text 'Your account has exceeded the storage quota "
            "(10 GB). Please upgrade your plan or free up space.'\n"
            "  [5] text 'Current usage: 10.2 GB / 10 GB'\n"
            "[6] button 'Upgrade Plan'\n"
            "[7] button 'Manage Storage'\n"
            "[8] link 'View Billing'"
        ),
        "elements": {
            "1": "heading Quota Exceeded",
            "2": "text limit reached message",
            "3": "alert Storage Limit Reached",
            "4": "text quota description",
            "5": "text usage stats",
            "6": "button Upgrade Plan",
            "7": "button Manage Storage",
            "8": "link View Billing",
        },
    }
