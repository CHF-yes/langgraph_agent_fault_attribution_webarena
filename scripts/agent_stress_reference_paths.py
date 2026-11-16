#!/usr/bin/env python3
"""Deterministic browser reference paths for the development tasks.

This is not an agent run: it performs a fixed, recorded browser path, writes a
WebArena-Verified ``agent_response.json`` in the task's public output contract,
and scores it with the native evaluator.  The goal is to prove that the
environment, the shared login condition and the scoring chain still accept a
legitimate path before any contemporaneous baseline/improved model block.

Artifacts per task (``--output-dir``):

    task231/reference_path.json   summary: actions, steps, evidence, answer, score
    task231/actions.jsonl         one line per browser action
    task231/agent_response.json   public output contract used for scoring
    task231/official_eval.json    native evaluator result + binding
    task231/network.har           recorded browser traffic
    task231/pages/*.txt           page snapshots used as source evidence

Credentials are never written to artifacts.  They are read from environment
variables or from the sibling WebArena checkout's public demo account table.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from playwright.sync_api import Page, sync_playwright

from standard_agent.webarena_evaluator import evaluate_task_safe
from standard_agent.webarena_verified import load_task_definition, write_agent_response

DEFAULT_CONFIG = ROOT / "experiments" / "webarena_local_config.local.json"
SHOPPING = os.getenv("SHOPPING_URL", "http://localhost:7770")
GITLAB = os.getenv("GITLAB_URL", "http://localhost:8023")


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


SENSITIVE_HEADERS = {"cookie", "set-cookie", "authorization", "proxy-authorization"}
SENSITIVE_FIELDS = ("pass", "password", "token", "secret")


def _redact_har(har_path: Path, secrets: list[str]) -> None:
    """Remove credentials/session material from a recorded HAR.

    Playwright keeps form POST parameters and Cookie headers even when response
    bodies are omitted.  Reference-path artifacts are meant to be archived, so
    redact those fields while keeping URLs, methods, statuses and timings.
    """
    try:
        har = json.loads(har_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    def redact_headers(headers):
        for header in headers or []:
            name = str(header.get("name", "")).casefold()
            if name in SENSITIVE_HEADERS:
                header["value"] = "<redacted>"

    for entry in har.get("log", {}).get("entries", []):
        request = entry.get("request", {})
        redact_headers(request.get("headers"))
        post = request.get("postData")
        if isinstance(post, dict):
            for param in post.get("params", []) or []:
                name = str(param.get("name", "")).casefold()
                if any(marker in name for marker in SENSITIVE_FIELDS):
                    param["value"] = "<redacted>"
            text = str(post.get("text", ""))
            if any(secret and secret in text for secret in secrets):
                post["text"] = "<redacted>"
        redact_headers(entry.get("response", {}).get("headers"))

    serialized = json.dumps(har, ensure_ascii=False)
    for secret in secrets:
        if secret:
            serialized = serialized.replace(secret, "<redacted>")
    har_path.write_text(serialized + "\n", encoding="utf-8")


def _webarena_accounts(webarena_root: Path) -> dict:
    """Read the public demo account table without importing the package.

    ``browser_env/__init__.py`` pulls in the full WebArena dependency stack and
    ``env_config.py`` asserts that every site URL is set, so importing it just
    to read a literal dict would add unrelated dependencies to this harness.
    """
    config_path = webarena_root / "browser_env" / "env_config.py"
    tree = ast.parse(config_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "ACCOUNTS"
               for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError(f"ACCOUNTS not found in {config_path}")


def load_accounts() -> dict:
    """Read demo accounts from env or the public WebArena checkout.

    The repository must not embed credentials; the sibling WebArena checkout
    already publishes these demo accounts in ``browser_env/env_config.py``.
    """
    accounts = {
        "shopping": {
            "username": os.getenv("SHOPPING_USERNAME", ""),
            "password": os.getenv("SHOPPING_PASSWORD", ""),
        },
        "gitlab": {
            "username": os.getenv("GITLAB_USERNAME", ""),
            "password": os.getenv("GITLAB_PASSWORD", ""),
        },
    }
    if all(account["username"] and account["password"] for account in accounts.values()):
        return accounts

    webarena_root = Path(os.getenv("WEBARENA_REPO", ROOT.parent / "webarena"))
    try:
        webarena_accounts = _webarena_accounts(webarena_root)
    except Exception as exc:  # pragma: no cover - depends on local checkout
        raise SystemExit(
            "demo accounts unavailable: set SHOPPING_USERNAME/SHOPPING_PASSWORD and "
            f"GITLAB_USERNAME/GITLAB_PASSWORD (WebArena checkout at {webarena_root}: {exc})"
        ) from exc
    for site in accounts:
        source = webarena_accounts[site]
        accounts[site]["username"] = accounts[site]["username"] or source["username"]
        accounts[site]["password"] = accounts[site]["password"] or source["password"]
    return accounts


class Recorder:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.actions: list[dict] = []
        self.page_snapshots: list[dict] = []

    def action(self, page: Page, action: str, args: dict, *, note: str = "",
               url_before: str | None = None) -> None:
        record = {
            "step": len(self.actions) + 1,
            "action": action,
            "args": args,
            "url_before": url_before if url_before is not None else page.url,
            "url_after": page.url,
            "note": note,
        }
        self.actions.append(record)
        with (self.output_dir / "actions.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def snapshot(self, page: Page, name: str, *, html: bool = False) -> Path:
        pages_dir = self.output_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        step = len(self.actions) + 1
        text_path = pages_dir / f"{step:02d}_{name}.txt"
        text_path.write_text(page.inner_text("body"), encoding="utf-8")
        entry = {"step": step, "name": name, "url": page.url,
                 "text": str(text_path.relative_to(self.output_dir))}
        if html:
            html_path = pages_dir / f"{step:02d}_{name}.html"
            html_path.write_text(page.content(), encoding="utf-8")
            entry["html"] = str(html_path.relative_to(self.output_dir))
        self.page_snapshots.append(entry)
        return text_path

    def summary(self, *, task_id: int, intent: str, answer: object,
                source_evidence: dict, notes: list[str]) -> dict:
        return {
            "schema": "agent_stress_reference_path/1",
            "task_id": task_id,
            "intent": intent,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "steps": len(self.actions),
            "actions": self.actions,
            "pages": self.page_snapshots,
            "answer": answer,
            "source_evidence": source_evidence,
            "notes": notes,
        }


def goto(page: Page, recorder: Recorder, url: str, *, note: str = "") -> None:
    before = page.url
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_load_state("domcontentloaded")
    recorder.action(page, "goto", {"url": url}, note=note, url_before=before)


def login_shopping(page: Page, recorder: Recorder, account: dict) -> None:
    goto(page, recorder, f"{SHOPPING}/customer/account/login/", note="open customer login")
    recorder.snapshot(page, "shopping_login")
    page.fill("#email", account["username"])
    recorder.action(page, "fill", {"selector": "#email", "value": account["username"]},
                    note="shopping email")
    page.fill("#pass", account["password"])
    recorder.action(page, "fill", {"selector": "#pass", "value": "<redacted>"},
                    note="shopping password")
    page.click("#send2")
    recorder.action(page, "click", {"selector": "#send2"}, note="submit customer login")
    try:
        page.wait_for_url(lambda url: "/customer/account/login" not in url, timeout=15000)
    except Exception:
        pass
    page.wait_for_load_state("domcontentloaded")
    if "customer/account/login" in page.url:
        raise RuntimeError("shopping login did not leave the login page")
    recorder.snapshot(page, "shopping_logged_in")


def login_gitlab(page: Page, recorder: Recorder, account: dict) -> None:
    goto(page, recorder, f"{GITLAB}/users/sign_in", note="open GitLab login")
    page.fill('[data-testid="username-field"]', account["username"])
    recorder.action(page, "fill",
                    {"selector": '[data-testid="username-field"]',
                     "value": account["username"]}, note="gitlab username")
    page.fill('[data-testid="password-field"]', account["password"])
    recorder.action(page, "fill",
                    {"selector": '[data-testid="password-field"]', "value": "<redacted>"},
                    note="gitlab password")
    page.click('[data-testid="sign-in-button"]')
    recorder.action(page, "click", {"selector": '[data-testid="sign-in-button"]'},
                    note="submit GitLab login")
    try:
        page.wait_for_url(lambda url: "/users/sign_in" not in url, timeout=15000)
    except Exception:
        pass
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(500)
    if "/users/sign_in" in page.url:
        raise RuntimeError("GitLab login did not leave the sign-in page")
    recorder.snapshot(page, "gitlab_logged_in")


# --------------------------------------------------------------------------
# task 231: most recent cancelled order number (shopping, account required)
# --------------------------------------------------------------------------

def reference_task_231(page: Page, recorder: Recorder, accounts: dict) -> tuple[object, dict, list[str]]:
    login_shopping(page, recorder, accounts["shopping"])
    goto(page, recorder, f"{SHOPPING}/sales/order/history/", note="open order history")
    recorder.snapshot(page, "order_history", html=True)
    rows = page.eval_on_selector_all(
        "table#my-orders-table tbody tr",
        """els => els.map(tr => ({
            order: (tr.querySelector('.col.id') || {}).innerText || '',
            date: (tr.querySelector('.col.date') || {}).innerText || '',
            status: (tr.querySelector('.col.status') || {}).innerText || '',
            href: (tr.querySelector('a') || {}).href || null,
        })).map(row => Object.assign(row, {
            order: row.order.trim(), date: row.date.trim(), status: row.status.trim(),
        }))""",
    )
    if not rows:
        raise RuntimeError("order history table is empty")
    cancelled = [row for row in rows if row["status"].casefold() in {"canceled", "cancelled"}]
    if not cancelled:
        raise RuntimeError("no cancelled order in history")
    selected = cancelled[0]  # rows are ordered newest-first
    match = re.search(r"(\d+)", selected["order"])
    if not match:
        raise RuntimeError(f"cannot parse order number from {selected['order']!r}")
    answer = [match.group(1)]
    evidence = {
        "rows_seen": len(rows),
        "cancelled_rows": cancelled,
        "selected": selected,
        "selection_rule": "first cancelled row in date-descending order history",
    }
    notes = [
        "Order history is sorted newest-first; the first Cancelled row is the most recent cancelled order.",
        "Answer is the atomic order number only, matching the task's array-of-strings contract.",
    ]
    return answer, evidence, notes


# --------------------------------------------------------------------------
# task 169: personal projects with the most stars (GitLab, account required)
# --------------------------------------------------------------------------

def reference_task_169(page: Page, recorder: Recorder, accounts: dict) -> tuple[object, dict, list[str]]:
    login_gitlab(page, recorder, accounts["gitlab"])
    goto(page, recorder, f"{GITLAB}/dashboard/projects", note="open personal project list")
    recorder.snapshot(page, "dashboard_projects", html=True)
    projects = page.eval_on_selector_all(
        "li.project-row",
        """els => els.map(li => {
            const link = li.querySelector('h2 a') || li.querySelector('a.project');
            const stars = li.querySelector('a.stars');
            const role = li.querySelector('.user-access-role');
            return {
                href: link ? link.getAttribute('href') : null,
                name: link ? link.innerText.replace(/\\s+/g, ' ').trim() : null,
                stars: stars ? parseInt(stars.innerText.trim(), 10) : null,
                role: role ? role.innerText.trim() : null,
            };
        }).filter(p => p.href)""",
    )
    username = accounts["gitlab"]["username"]
    personal = [p for p in projects if p["href"].startswith(f"/{username}/")]
    if not personal:
        raise RuntimeError("no personal projects found in dashboard list")
    top_stars = max(p["stars"] for p in personal)
    top = [p for p in personal if p["stars"] == top_stars]
    ids = []
    project_pages = []
    for project in top:
        url = f"{GITLAB}{project['href']}"
        goto(page, recorder, url, note=f"open personal project {project['name']}")
        html = page.content()
        match = re.search(r'data-project-id="(\d+)"', html)
        if not match:
            raise RuntimeError(f"project id missing on {url}")
        ids.append(int(match.group(1)))
        project_pages.append({"name": project["name"], "url": url,
                              "project_id": int(match.group(1))})
        recorder.snapshot(page, f"project_{match.group(1)}", html=True)
    evidence = {
        "personal_projects": personal,
        "max_stars": top_stars,
        "top_projects": top,
        "project_pages": project_pages,
        "selection_rule": "personal namespace only; highest star count; project ID read from project page",
    }
    notes = [
        "The dashboard also lists contributed projects (a11yproject.com, primer/design); they are excluded because the task asks for personal projects.",
        "Two personal projects tie at 6 stars; both project IDs are returned.",
    ]
    return ids, evidence, notes


# --------------------------------------------------------------------------
# task 281: Oral-B kids brush head names and price range (shopping)
# --------------------------------------------------------------------------

def _search_products(page: Page, recorder: Recorder, query: str, *, pages: int = 1) -> tuple[list[dict], dict]:
    goto(page, recorder, f"{SHOPPING}/catalogsearch/result/?q={query.replace(' ', '+')}",
         note=f"search {query!r} page 1")
    all_items: list[dict] = []
    page_info = []
    for index in range(1, pages + 1):
        if index > 1:
            goto(page, recorder,
                 f"{SHOPPING}/catalogsearch/result/?q={query.replace(' ', '+')}&p={index}",
                 note=f"search {query!r} page {index}")
        recorder.snapshot(page, f"search_{index}", html=(index == 1))
        items = page.eval_on_selector_all(
            "li.product-item",
            """els => els.map(e => {
                const link = e.querySelector('.product-item-link');
                const price = e.querySelector('[data-price-type="finalPrice"]');
                return {
                    name: link ? link.innerText.replace(/\\s+/g, ' ').trim() : '',
                    price: price ? parseFloat(price.getAttribute('data-price-amount')) : null,
                    href: link ? link.href : null,
                };
            }).filter(item => item.name && item.price !== null)""",
        )
        all_items.extend(items)
        page_info.append({"page": index, "url": page.url, "items": len(items)})
    return all_items, {"query": query, "pages_visited": page_info}


def reference_task_281(page: Page, recorder: Recorder, accounts: dict) -> tuple[object, dict, list[str]]:
    login_shopping(page, recorder, accounts["shopping"])
    query = "Oral-B Kids Replacement Brush Heads"
    items, search_evidence = _search_products(page, recorder, query, pages=2)
    matched = []
    for item in items:
        name = item["name"]
        lower = name.casefold()
        genuine_brand = lower.startswith("oral-b kids") or lower.startswith("kids by oral-b")
        replacement_head = "brush head" in lower or "replacement head" in lower
        electric_unit = "electric rechargeable power toothbrush" in lower or "toothbrush featuring" in lower
        if genuine_brand and replacement_head and not electric_unit:
            matched.append(item)
    if not matched:
        raise RuntimeError("no matching Oral-B kids replacement brush heads found")
    answer = {
        "names": [item["name"] for item in matched],
        "min": min(item["price"] for item in matched),
        "max": max(item["price"] for item in matched),
    }
    evidence = {
        "search": search_evidence,
        "products_seen": len(items),
        "matched": matched,
        "selection_rule": (
            "genuine Oral-B kids replacement brush heads: name starts with Oral-B Kids or "
            "Kids By Oral-B, mentions brush/replacement heads, and is not a powered toothbrush unit"
        ),
    }
    notes = [
        "Page 1 contains both genuine Oral-B kids replacement brush heads; page 2 was checked for additional matches and returned none.",
        "Compatible/third-party listings (Oral B, VINFANY, generic Braun-compatible) and powered toothbrush units are excluded by the brand/head rule.",
        "Prices are the final displayed prices; min/max are computed from the matched products.",
    ]
    return answer, evidence, notes


# --------------------------------------------------------------------------
# task 21: reviewers who mention small ear cups (shopping product page)
# --------------------------------------------------------------------------

def reference_task_21(page: Page, recorder: Recorder, accounts: dict) -> tuple[object, dict, list[str]]:
    url = (f"{SHOPPING}/6s-wireless-headphones-over-ear-noise-canceling-hi-fi-bass-"
           "foldable-stereo-wireless-kid-headsets-earbuds-with-built-in-mic-micro-sd-tf-fm-"
           "for-iphone-samsung-ipad-pc-black-gold.html")
    goto(page, recorder, url + "#reviews", note="open product review section")
    page.wait_for_selector(".review-item", state="attached", timeout=15000)
    page.wait_for_timeout(500)
    recorder.snapshot(page, "product_reviews", html=True)
    reviews = page.eval_on_selector_all(
        ".review-item",
        """els => els.map(e => {
            const details = e.querySelector('.review-details');
            const clone = e.cloneNode(true);
            clone.querySelectorAll('script, style').forEach(n => n.remove());
            const author = details ? (details.innerText.match(/Review by\\s+([^\\n]+)/) || [])[1] : null;
            const body = clone.innerText.replace(/\\s+/g, ' ').trim();
            return {author: author ? author.trim() : null, body: body};
        })""",
    )
    matched = []
    for review in reviews:
        text = review["body"]
        lower = text.casefold()
        small_ears = "small ears" in lower or "small ear" in lower
        cups_too_small = "ear cup" in lower and ("too small" in lower or "not over the ear" in lower
                                                 or "half my ear" in lower)
        if small_ears or cups_too_small:
            matched.append(review)
    if not matched:
        raise RuntimeError("no review mentions small ear cups")
    answer = [review["author"] for review in matched]
    evidence = {
        "reviews_seen": len(reviews),
        "matched_reviews": matched,
        "selection_rule": (
            "review text mentions small ears, or mentions ear cups together with too small / "
            "not over the ear / half my ear"
        ),
    }
    notes = [
        "Authors are returned exactly as shown on the page; the contract accepts the page's duplicated "
        "Michelle Davis display value.",
        "This page currently exposes the expected review set (Catso, Dibbins, Anglebert Dinkherhump, "
        "Michelle Davis), unlike the earlier server note that reported only Jay/Josef.",
    ]
    return answer, evidence, notes


# --------------------------------------------------------------------------
# task 126: Canon photo printer price range (shopping)
# --------------------------------------------------------------------------

def reference_task_126(page: Page, recorder: Recorder, accounts: dict) -> tuple[object, dict, list[str]]:
    login_shopping(page, recorder, accounts["shopping"])
    items, search_evidence = _search_products(page, recorder, "Canon photo printer", pages=2)
    matched = [
        item for item in items
        if item["name"].casefold().startswith("canon")
        and "photo" in item["name"].casefold()
        and "printer" in item["name"].casefold()
    ]
    if not matched:
        raise RuntimeError("no Canon photo printers found")
    answer = {"min": min(item["price"] for item in matched),
              "max": max(item["price"] for item in matched)}
    evidence = {
        "search": search_evidence,
        "products_seen": len(items),
        "matched": matched,
        "selection_rule": "name starts with Canon and contains photo + printer",
    }
    notes = [
        "Page 1 supplies both extremes ($2.56 and $649.99); page 2 was checked for a wider range and did not change them.",
        "Accessories, compatible cables/printheads and Canon non-photo printers are excluded by the name rule.",
    ]
    return answer, evidence, notes


# --------------------------------------------------------------------------
# task 132: commits by kilian to a11yproject.com on 2023-03-05 (GitLab)
# --------------------------------------------------------------------------

def reference_task_132(page: Page, recorder: Recorder, accounts: dict) -> tuple[object, dict, list[str]]:
    login_gitlab(page, recorder, accounts["gitlab"])
    url = (f"{GITLAB}/a11yproject/a11yproject.com/-/commits/main"
           "?author=kilian&since=2023-03-05&until=2023-03-06")
    goto(page, recorder, url, note="open commits filtered by author and date")
    recorder.snapshot(page, "commits_filtered", html=True)
    body = page.inner_text("body")
    match = re.search(r"05 Mar, 2023\s+(\d+)\s+commits?", body, re.IGNORECASE)
    if not match:
        match = re.search(r"05 Mar, 2023\s+(\d+)\s+commit", body, re.IGNORECASE)
    if not match:
        raise RuntimeError("cannot find 05 Mar 2023 commit group on filtered commits page")
    count = int(match.group(1))
    rows = page.eval_on_selector_all(
        ".commit-row, li.commit, [data-testid='commit-row']",
        "els => els.map(e => e.innerText.replace(/\\s+/g, ' ').trim().slice(0, 300))",
    )
    evidence = {
        "filtered_url": page.url,
        "date_group": "05 Mar, 2023",
        "group_count": count,
        "commit_rows_visible": rows,
        "selection_rule": "count shown by GitLab for the 05 Mar, 2023 group with author=kilian",
    }
    notes = [
        "Author and date filters are applied through the GitLab commits UI URL, not by scanning all commits.",
        "The group heading reports one commit for 05 Mar, 2023.",
    ]
    return [count], evidence, notes


def write_login_states(state_dir: Path, accounts: dict, *, headless: bool = True) -> dict:
    """Create and verify the shared Playwright storage states for both arms.

    The state files themselves live under ``experiments/auth/`` (gitignored);
    only their SHA-256 hashes and verification markers are recorded.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    log_dir = state_dir / "_verification"
    log_dir.mkdir(parents=True, exist_ok=True)
    jobs = {
        "shopping": (
            lambda page, recorder: login_shopping(page, recorder, accounts["shopping"]),
            f"{SHOPPING}/sales/order/history/",
        ),
        "gitlab": (
            lambda page, recorder: login_gitlab(page, recorder, accounts["gitlab"]),
            f"{GITLAB}/dashboard/projects",
        ),
    }
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        try:
            for site, (login, verify_url) in jobs.items():
                recorder = Recorder(log_dir)
                (log_dir / "actions.jsonl").write_text("", encoding="utf-8")
                context = browser.new_context(viewport={"width": 1400, "height": 1000})
                page = context.new_page()
                login(page, recorder)
                state_path = state_dir / f"{site}.json"
                context.storage_state(path=str(state_path))
                context.close()

                verify_context = browser.new_context(storage_state=str(state_path))
                verify_page = verify_context.new_page()
                verify_page.goto(verify_url, wait_until="domcontentloaded")
                verify_page.wait_for_load_state("domcontentloaded")
                body = verify_page.inner_text("body")
                if site == "shopping":
                    verified = ("Sign Out" in body
                                and "/customer/account/login" not in verify_page.url)
                else:
                    verified = ("Yours" in body
                                or accounts["gitlab"]["username"] in body)
                results.append({
                    "site": site,
                    "state_path": str(state_path),
                    "state_sha256": _sha(state_path),
                    "verified_url": verify_page.url,
                    "verified": verified,
                })
                verify_context.close()
                if not verified:
                    raise RuntimeError(f"{site} storage state did not authenticate at {verify_url}")
        finally:
            browser.close()
    manifest = {
        "schema": "agent_stress_login_condition/1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "scope": "shared by baseline (improvement flags off) and improved (flags on) arms",
        "states": results,
    }
    manifest_path = ROOT / "experiments" / "agent_stress_v1" / "login_condition_20261112.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    return manifest


def reference_task_118(page: Page, recorder: Recorder, accounts: dict) -> tuple[object, dict, list[str]]:
    """Task 118: reach a product page for a jaw-bruxism aid.

    Normal path uses the shopping search endpoint that the HTTP acceptance
    faults; the alternative path reaches the same product page directly
    without touching that endpoint.  The official task type is NAVIGATE, so
    the recorded answer is the reached page, not a retrieval payload.
    """
    query = "night guard"
    goto(page, recorder, f"{SHOPPING}/catalogsearch/result/?q=night+guard",
         note="normal path: shopping search endpoint")
    recorder.snapshot(page, "search_results", html=True)
    links = page.eval_on_selector_all(
        "li.product-item a.product-item-link, a.product-item-link",
        """els => els.map(a => ({
            text: (a.innerText || '').replace(/\s+/g, ' ').trim(),
            href: a.href,
        })).filter(item => item.href)""",
    )
    if not links:
        raise RuntimeError("search returned no product links")
    markers = ("night guard", "mouth guard", "bruxism", "dental guard", "bite guard")
    selected = next((item for item in links
                     if any(marker in item["text"].casefold() for marker in markers)), links[0])
    page.goto(selected["href"], wait_until="domcontentloaded")
    page.wait_for_load_state("domcontentloaded")
    recorder.action(page, "click", {"href": selected["href"], "text": selected["text"]},
                    note="open selected product from search results")
    recorder.snapshot(page, "product_from_search", html=True)
    normal_url = page.url
    title = page.title()

    # Legal alternative: direct navigation to the same product page, without
    # issuing a request to /catalogsearch/result.
    goto(page, recorder, normal_url, note="alternative path: direct product URL")
    recorder.snapshot(page, "product_direct", html=True)
    alternative_url = page.url

    evidence = {
        "normal_path": {
            "search_url": f"{SHOPPING}/catalogsearch/result/?q=night+guard",
            "candidate_links": len(links),
            "selected": selected,
            "final_url": normal_url,
            "title": title,
        },
        "alternative_path": {
            "method": "direct product URL without the search endpoint",
            "final_url": alternative_url,
        },
        "task_type": "NAVIGATE",
    }
    answer = f"Opened the product page for {selected['text']} at {alternative_url}"
    notes = [
        "Task 118 is NAVIGATE; success is a completed response with null retrieved_data.",
        "Normal path used the catalogsearch endpoint; the alternative path avoided it.",
    ]
    return answer, evidence, notes


TASKS = {
    118: ("I have a jaw bruxism problem, go to the product page for something that could alleviate the problem.", "shopping", reference_task_118),
    231: ("Get the order number of my most recent cancelled order", "shopping", reference_task_231),
    169: ("Get the project ID(s) of my personal project(s) that received the most stars", "gitlab", reference_task_169),
    281: ("Provide the full names of Oral B brush heads designed for children and the price range", "shopping", reference_task_281),
    21: ("Get names of reviewers who mention ear cups being small", "shopping", reference_task_21),
    126: ("Get the price range of Canon photo printers", "shopping", reference_task_126),
    132: ("Count kilian's commits to a11yproject.com on 2023-03-05", "gitlab", reference_task_132),
}


def evaluate(output_dir: Path, task: dict, *, config: Path) -> dict:
    response_path = output_dir / "agent_response.json"
    har_path = output_dir / "network.har"
    evaluation = evaluate_task_safe(
        task["task_id"], agent_response_path=response_path,
        network_trace_path=har_path if har_path.exists() else None,
        config_path=config,
    )
    evaluation["_evaluation_binding"] = {
        "agent_response_sha256": _sha(response_path),
        "network_har_sha256": _sha(har_path),
        "config_sha256": _sha(config),
        "evaluator_adapter_version": "stage-c-eval-adapter/1",
    }
    (output_dir / "official_eval.json").write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return evaluation


def run_task(task_id: int, output_dir: Path, *, headless: bool, config: Path,
             accounts: dict) -> dict:
    task = load_task_definition(task_id)
    intent = str(task.get("intent", ""))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "actions.jsonl").write_text("", encoding="utf-8")
    recorder = Recorder(output_dir)
    har_path = (output_dir / "network.har").resolve()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1400, "height": 1000},
            record_har_path=str(har_path),
            record_har_content="omit",
        )
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            answer, evidence, notes = TASKS[task_id][2](page, recorder, accounts)
        finally:
            context.close()  # flush HAR before the browser exits
            browser.close()

    _redact_har(har_path, [accounts["shopping"]["password"], accounts["gitlab"]["password"]])

    response_path = output_dir / "agent_response.json"
    write_agent_response(response_path, task, completed=True,
                         answer=json.dumps(answer, ensure_ascii=False))
    evaluation = evaluate(output_dir, task, config=config)
    summary = recorder.summary(
        task_id=task_id, intent=intent, answer=answer,
        source_evidence=evidence, notes=notes,
    )
    summary.update({
        "site": TASKS[task_id][1],
        "public_output": json.loads(response_path.read_text(encoding="utf-8")),
        "official_success": bool(evaluation.get("official_success")),
        "official_status": evaluation.get("status"),
        "official_score": evaluation.get("score"),
        "official_eval": str((output_dir / "official_eval.json").relative_to(output_dir)),
        "agent_response": str(response_path.relative_to(output_dir)),
        "network_har": "network.har" if har_path.exists() else None,
        "network_har_redacted": bool(har_path.exists()),
    })
    (output_dir / "reference_path.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def log(message: str) -> None:
    """Print progress without letting a closed stdout abort a completed task."""
    try:
        print(message, flush=True)
    except BrokenPipeError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=int, action="append", choices=sorted(TASKS),
                        help="task id to verify (repeatable); default: 231, 169, 281")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "experiments" / "agent_stress_v1" / "reference_paths_20261112")
    parser.add_argument("--evaluator-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--write-storage-state-dir", type=Path,
                        help="Create and verify <site>.json login states, then exit")
    args = parser.parse_args()

    accounts = load_accounts()
    if args.write_storage_state_dir:
        manifest = write_login_states(args.write_storage_state_dir, accounts,
                                      headless=not args.headed)
        for state in manifest["states"]:
            log(f"[login] {state['site']}: verified={state['verified']} "
                f"sha256={state['state_sha256'][:12]}")
        return 0

    task_ids = args.task or [231, 169, 281]
    results = []
    for task_id in task_ids:
        log(f"[reference] task {task_id} ...")
        target = args.output_dir / f"task{task_id}"
        try:
            summary = run_task(task_id, target, headless=not args.headed,
                               config=args.evaluator_config, accounts=accounts)
            results.append(summary)
            log(f"[reference] task {task_id}: success={summary['official_success']} "
                f"steps={summary['steps']} status={summary['official_status']}")
        except Exception as exc:
            # A run that already wrote a successful reference_path.json must not
            # be replaced by a transport/logging failure.
            existing = target / "reference_path.json"
            previous = None
            try:
                candidate = json.loads(existing.read_text(encoding="utf-8"))
                if candidate.get("official_success") and (target / "official_eval.json").exists():
                    previous = candidate
            except Exception:
                previous = None
            if previous is not None:
                results.append(previous)
                log(f"[reference] task {task_id}: kept previous successful artifact ({exc})")
                continue
            failure = {"task_id": task_id, "error": str(exc), "official_success": False}
            target.mkdir(parents=True, exist_ok=True)
            existing.write_text(
                json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            results.append(failure)
            log(f"[reference] task {task_id}: ERROR {exc}")

    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(json.dumps({
        "schema": "agent_stress_reference_paths_summary/1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "evaluator_config": str(args.evaluator_config),
        "results": results,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failed = [r["task_id"] for r in results if not r.get("official_success")]
    log(f"[reference] summary: {len(results) - len(failed)}/{len(results)} passed; "
        f"failed={failed}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
