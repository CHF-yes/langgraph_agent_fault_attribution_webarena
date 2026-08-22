"""
WebArena Benchmark 站点配置。

WebArena 的每个站点运行在独立的 Docker 容器中。配置包含：
- 站点 URL（Docker 容器地址）
- 站点描述
- 常用元素选择器提示（帮助 agent 理解页面结构）

使用方式：
    1. 启动 WebArena Docker 环境（见下方说明）
    2. 修改 BASE_URL 指向你的 Docker 主机
    3. 运行 python main.py --site shopping --task "..."

WebArena 环境搭建：
    git clone https://github.com/web-arena-team/web-arena.git
    cd web-arena
    # 按官方文档启动 Docker 容器
    # 各站点默认映射到 localhost 的不同端口
"""

import os

# ============================================================
# Docker 主机地址配置
# ============================================================
# 如果 Docker 运行在本机，使用 localhost
# 如果在远程服务器，改为对应 IP
WEBARENA_HOST = os.getenv("WEBARENA_HOST", "localhost")

# ============================================================
# 各站点配置
# ============================================================

WEBARENA_SITES = {
    "shopping": {
        "name": "Shopping (OneStopShop)",
        "base_url": os.getenv(
            "SHOPPING_URL",
            f"http://{WEBARENA_HOST}:7770"
        ),
        "description": (
            "E-commerce website with products, cart, checkout, "
            "and order history. Tasks include searching products, "
            "comparing prices, adding to cart, and checking order status."
        ),
        "login_url": "/",
        "hints": [
            "Product search box is usually at the top",
            "Cart link is in the header navigation",
            "Product prices are in text nodes below product names",
            "Use the search box ([id] textbox 'Search') to find products",
        ],
    },
    "shopping_admin": {
        "name": "Shopping Admin",
        "base_url": os.getenv(
            "SHOPPING_ADMIN_URL",
            f"http://{WEBARENA_HOST}:7780/admin"
        ),
        "description": (
            "Admin panel for the shopping site. Tasks include managing "
            "products, viewing orders, and updating inventory."
        ),
        "login_url": "/admin",
        "hints": [
            "Login with admin credentials first if required",
            "Product list is in a table format",
            "Look for 'Orders', 'Products', 'Customers' in navigation",
        ],
    },
    "reddit": {
        "name": "Reddit (Social Forum)",
        "base_url": os.getenv(
            "REDDIT_URL",
            f"http://{WEBARENA_HOST}:9999"
        ),
        "description": (
            "Social forum with subreddits, posts, comments, and voting. "
            "Tasks include finding specific posts, reading comments, "
            "posting replies, and navigating subreddits."
        ),
        "login_url": "/login",
        "hints": [
            "Search bar at the top for finding subreddits/posts",
            "Post titles are links",
            "Comments are below each post",
            "Use 'submit' button to create new posts",
        ],
    },
    "gitlab": {
        "name": "GitLab (Code Repository)",
        "base_url": os.getenv(
            "GITLAB_URL",
            f"http://{WEBARENA_HOST}:8023"
        ),
        "description": (
            "Git repository hosting with projects, issues, merge requests. "
            "Tasks include finding repositories, reading files, "
            "checking commit history, and managing issues."
        ),
        "login_url": "/users/sign_in",
        "hints": [
            "Repository list is on the dashboard after login",
            "File tree is in the left sidebar of each project",
            "Issues and Merge Requests tabs are in project navigation",
            "Use the search bar to find projects",
        ],
    },
    "map": {
        "name": "Map (OpenStreetMap)",
        "base_url": os.getenv(
            "MAP_URL",
            f"http://{WEBARENA_HOST}:3000"
        ),
        "description": (
            "Interactive map with search, directions, and location info. "
            "Tasks include finding locations, getting directions, "
            "and checking distances between places."
        ),
        "login_url": None,
        "hints": [
            "Search box at the top left for finding locations",
            "Directions panel appears after searching for a route",
            "Click on map markers for location details",
            "Use 'Directions' button for route planning",
        ],
    },
    "wikipedia": {
        "name": "Wikipedia (Knowledge Base)",
        "base_url": os.getenv(
            "WIKIPEDIA_URL",
            f"http://{WEBARENA_HOST}:8888"
        ),
        "description": (
            "Online encyclopedia with articles, categories, and references. "
            "Tasks include finding information in articles, "
            "comparing facts, and navigating between related topics."
        ),
        "login_url": None,
        "hints": [
            "Search box in the top right corner",
            "Article content is in the main area",
            "Table of contents (if present) is after the lead section",
            "Infobox on the right side has key facts",
        ],
    },
    "cms": {
        "name": "CMS (WordPress)",
        "base_url": os.getenv(
            "CMS_URL",
            f"http://{WEBARENA_HOST}:8080"
        ),
        "description": (
            "Content management system for creating and managing web content. "
            "Tasks include creating/editing posts, managing pages, "
            "and configuring site settings."
        ),
        "login_url": "/wp-login.php",
        "hints": [
            "Admin panel is at /wp-admin after login",
            "Posts menu in the left sidebar",
            "Use 'Add New' button to create content",
            "Publish button in the top right of the editor",
        ],
    },
}


# WebArena-Verified 镜像的 HTTP header 自动登录配置
# （镜像通过 header 认证绕过 UI 登录，见官方环境文档）
AUTO_LOGIN_HEADERS = {
    "shopping": {
        "X-M2-Customer-Auto-Login": os.getenv("SHOPPING_AUTO_LOGIN", ""),
    },
    "shopping_admin": {
        "X-M2-Admin-Auto-Login": os.getenv("SHOPPING_ADMIN_AUTO_LOGIN", ""),
    },
    "reddit": {
        "X-Postmill-Auto-Login": os.getenv("REDDIT_AUTO_LOGIN", ""),
    },
    # gitlab: 内置登录态，无需 header
}


def get_auto_login_headers(site_name: str) -> dict:
    """获取指定站点的自动登录 HTTP header（没有则返回空 dict）。"""
    return {
        key: value
        for key, value in AUTO_LOGIN_HEADERS.get(site_name, {}).items()
        if value
    }


def get_site_config(site_name: str) -> dict:
    """
    获取指定站点的配置。

    Args:
        site_name: 站点 key（如 "shopping", "reddit" 等）

    Returns:
        站点配置 dict

    Raises:
        KeyError: 站点不存在
    """
    if site_name not in WEBARENA_SITES:
        available = ", ".join(WEBARENA_SITES.keys())
        raise KeyError(
            f"Unknown site '{site_name}'. Available: {available}"
        )
    return WEBARENA_SITES[site_name]


def list_sites() -> list[dict]:
    """列出所有可用的 WebArena 站点。"""
    return [
        {"key": k, "name": v["name"], "url": v["base_url"]}
        for k, v in WEBARENA_SITES.items()
    ]


def get_task_prompt(site_name: str, task: str) -> str:
    """
    生成包含站点 hints 的 enhanced task prompt。

    Args:
        site_name: 站点 key
        task: 原始任务描述

    Returns:
        增强后的任务 prompt
    """
    site = get_site_config(site_name)
    hints_text = "\n".join(f"  - {h}" for h in site.get("hints", []))

    return f"""## Site: {site['name']}
## Site Description
{site['description']}

## Site-Specific Tips
{hints_text}

## Your Task
{task}"""
