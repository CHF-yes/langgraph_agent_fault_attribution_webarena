#!/usr/bin/env python3
"""Deterministic browser reference paths for the frozen 24-task roster.

For every task this runs two no-model browser paths and scores both with the
native WebArena-Verified evaluator:

* ``normal``: fixed browser navigations that read the task facts from the
  task-critical endpoint and submit the official output contract.
* ``recovery``: the same fault endpoint is hit once while the service-level
  persistent HTTP fault is armed (the agent-visible observation becomes 500/503),
  nine non-target navigations pass the 10-step window, and the endpoint is hit
  again after release.  The normal path is then completed and scored.

Artifacts per task:

    taskNN/normal/{actions.jsonl,pages/,agent_response.json,official_eval.json,network.har}
    taskNN/recovery/{actions.jsonl,fault_evidence.json,agent_response.json,official_eval.json,network.har}
    taskNN/reference_path.json
    taskNN/acceptance.json

Credentials come from the shared ``experiments/auth_20261120`` storage states.
"""
from __future__ import annotations

import argparse
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

from fault_injection.config import FaultConfig
from fault_injection.http_service import (
    EXPERIMENT_ID, ServiceEndpoint, classify_service_episode,
)
from standard_agent.environment.browser import SyncBrowserEnv
from standard_agent.environment.webarena_config import get_auto_login_headers
from standard_agent.webarena_evaluator import evaluate_task_safe
from standard_agent.webarena_verified import load_task_definition, write_agent_response

DATASET = Path(os.getenv("WEBARENA_DATASET",
                         "/data/webarena/webarena-dataset/webarena-verified.json"))
CONFIG = ROOT / "experiments" / "webarena_local_config.local.json"
AUTH_DIR = ROOT / "experiments" / "auth_20261120"
OUT = ROOT / "experiments" / "agent_stress_v1" / "formal_freeze_20261123" / "reference"
PORTS = {"shopping": 7770, "gitlab": 8023, "reddit": 9999}
SHOPPING = "http://localhost:7770"
GITLAB = "http://localhost:8023"
REDDIT = "http://localhost:9999"
MAX_STEPS = 20
WINDOW_STEPS = 10

ROSTER = [27, 28, 48, 49, 66, 69, 148, 149, 163, 164, 168, 188, 190, 206,
          227, 228, 294, 295, 305, 314, 316, 384, 784, 785]

SUPPLEMENT = {69, 149, 206, 168}
PILOT = {28, 228, 314}
FORUMS = {27: "personalfinance", 28: "WorcesterMA"}
BOOK_TASKS = {66, 69}
PRODUCT_TASKS = {163: 38805, 164: 6523}
ORDER_TOTAL_TASKS = {188: "Canceled", 190: "Complete"}
ORDER_AGG_TASKS = {48: ("complete", "2023-06-10", "2023-06-12"),
                   49: ("complete", "2023-02-12", "2023-06-12")}
ORDER_COLOR_TASKS = {148: ("2022-09", "Framed Wall Art|Picture Frame"),
                     149: ("2023-02", "Artificial Plants")}
SEARCH_BRANDS = {227: "EYZUTAK", 228: "sephora"}
REVIEW_NAMES_TASK = 384
PROJECT_SEARCH = {294: "ChatGPT", 295: "metaseq"}
GRAPH_TASKS = {
    314: ("primer/design", "main", "names"),
    316: ("facebook/create-react-app", "main", "emails"),
    784: ("CellularPrivacy/Android-IMSI-Catcher-Detector", "master", "email"),
    785: ("CellularPrivacy/Android-IMSI-Catcher-Detector", "gh-pages", "email"),
}
COMMIT_TASKS = {
    206: ("a11yproject/a11yproject.com", "Eric Bailey", "02 Mar, 2023"),
    305: ("a11yproject/a11yproject.com", "Philip Wong", "Jan 2023"),
}

ENDPOINTS = {
    27: f"{REDDIT}/f/personalfinance",
    28: f"{REDDIT}/f/WorcesterMA",
    48: f"{SHOPPING}/sales/order/history",
    49: f"{SHOPPING}/sales/order/history",
    66: f"{REDDIT}/f/books",
    69: f"{REDDIT}/f/books",
    148: f"{SHOPPING}/sales/order/history",
    149: f"{SHOPPING}/sales/order/history",
    163: f"{SHOPPING}/review/product/listAjax/id/38805",
    164: f"{SHOPPING}/review/product/listAjax/id/6523",
    168: f"{GITLAB}/dashboard/projects",
    188: f"{SHOPPING}/sales/order/history",
    190: f"{SHOPPING}/sales/order/history",
    206: f"{GITLAB}/a11yproject/a11yproject.com/-/commits/main",
    227: f"{SHOPPING}/catalogsearch/result",
    228: f"{SHOPPING}/catalogsearch/result",
    294: f"{GITLAB}/search",
    295: f"{GITLAB}/search",
    305: f"{GITLAB}/a11yproject/a11yproject.com/-/commits/main",
    314: f"{GITLAB}/primer/design/-/graphs",
    316: f"{GITLAB}/facebook/create-react-app/-/graphs",
    384: f"{SHOPPING}/catalogsearch/result",
    784: f"{GITLAB}/CellularPrivacy/Android-IMSI-Catcher-Detector/-/graphs",
    785: f"{GITLAB}/CellularPrivacy/Android-IMSI-Catcher-Detector/-/graphs",
}
TRIGGER_URLS = {
    27: f"{REDDIT}/f/personalfinance/new",
    28: f"{REDDIT}/f/WorcesterMA/new",
    48: f"{SHOPPING}/sales/order/history/",
    49: f"{SHOPPING}/sales/order/history/",
    66: f"{REDDIT}/f/books",
    69: f"{REDDIT}/f/books",
    148: f"{SHOPPING}/sales/order/history/",
    149: f"{SHOPPING}/sales/order/history/",
    163: f"{SHOPPING}/review/product/listAjax/id/38805/",
    164: f"{SHOPPING}/review/product/listAjax/id/6523/",
    168: f"{GITLAB}/dashboard/projects",
    188: f"{SHOPPING}/sales/order/history/",
    190: f"{SHOPPING}/sales/order/history/",
    206: f"{GITLAB}/a11yproject/a11yproject.com/-/commits/main?author=Eric+Bailey",
    227: f"{SHOPPING}/catalogsearch/result/?q=EYZUTAK",
    228: f"{SHOPPING}/catalogsearch/result/?q=sephora",
    294: f"{GITLAB}/search?search=ChatGPT&nav_source=navbar",
    295: f"{GITLAB}/search?search=metaseq&nav_source=navbar",
    305: f"{GITLAB}/a11yproject/a11yproject.com/-/commits/main?author=Philip+Wong",
    314: f"{GITLAB}/primer/design/-/graphs/main",
    316: f"{GITLAB}/facebook/create-react-app/-/graphs/main",
    384: f"{SHOPPING}/catalogsearch/result/?q=EYZUTAK+phone+case",
    784: f"{GITLAB}/CellularPrivacy/Android-IMSI-Catcher-Detector/-/graphs/master",
    785: f"{GITLAB}/CellularPrivacy/Android-IMSI-Catcher-Detector/-/graphs/gh-pages",
}
FILLERS = {
    "reddit": [f"{REDDIT}/forums", f"{REDDIT}/", f"{REDDIT}/forums/all"],
    "shopping": [f"{SHOPPING}/", f"{SHOPPING}/customer/account/",
                 f"{SHOPPING}/beauty-personal-care.html"],
    "gitlab": [f"{GITLAB}/explore", f"{GITLAB}/dashboard/projects",
               f"{GITLAB}/"],
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def task_meta(task_id: int) -> dict:
    task = load_task_definition(task_id, path=DATASET)
    site = (task.get("sites") or [None])[0]
    start = (task.get("start_urls") or [None])[0]
    if start:
        start = start.replace(f"__{site.upper()}__", f"http://localhost:{PORTS[site]}")
    return {"task_id": task_id, "site": site, "start_url": start,
            "intent": task["intent"],
            "intent_template_id": task.get("intent_template_id"),
            "definition": task}


class Recorder:
    def __init__(self, out: Path):
        self.out = out
        self.actions: list[dict] = []
        self.pages: list[dict] = []

    def action(self, action: str, args: dict, *, url_before: str, url_after: str,
               note: str = "", extra: dict | None = None) -> None:
        row = {"step": len(self.actions) + 1, "action": action, "args": args,
               "url_before": url_before, "url_after": url_after, "note": note}
        if extra:
            row.update(extra)
        self.actions.append(row)
        with (self.out / "actions.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def snapshot(self, text: str, html: str, *, name: str, url: str) -> None:
        pages = self.out / "pages"
        pages.mkdir(parents=True, exist_ok=True)
        step = len(self.actions)
        txt = pages / f"{step:02d}_{name}.txt"
        txt.write_text(text, encoding="utf-8")
        htm = pages / f"{step:02d}_{name}.html"
        htm.write_text(html, encoding="utf-8")
        self.pages.append({"step": step, "name": name, "url": url,
                           "text": str(txt.relative_to(self.out)),
                           "html": str(htm.relative_to(self.out))})


class PlaywrightActor:
    def __init__(self, page, recorder: Recorder):
        self.page = page
        self.rec = recorder

    @property
    def url(self) -> str:
        return self.page.url

    def goto(self, url: str, *, note: str = "") -> None:
        before = self.page.url
        self.page.goto(url, wait_until="domcontentloaded")
        self.page.wait_for_load_state("domcontentloaded")
        self.page.wait_for_timeout(150)
        self.rec.action("goto", {"url": url}, url_before=before,
                        url_after=self.page.url, note=note)

    def eval_all(self, selector: str, js: str):
        return self.page.eval_on_selector_all(selector, js)

    def text(self, selector: str = "body") -> str:
        return self.page.inner_text(selector)

    def content(self) -> str:
        return self.page.content()

    def wait(self, ms: int = 300) -> None:
        self.page.wait_for_timeout(ms)

    def snapshot(self, name: str) -> None:
        self.rec.snapshot(self.page.inner_text("body"), self.page.content(),
                          name=name, url=self.page.url)


class InjectActor:
    """Actor over SyncBrowserEnv + FaultInjector; reads the real DOM for facts."""

    def __init__(self, env: SyncBrowserEnv, injector, recorder: Recorder):
        self.env = env
        self.injector = injector
        self.rec = recorder

    @property
    def page(self):
        return self.env._env._page

    @property
    def url(self) -> str:
        return self.page.url

    def _run(self, coro):
        return self.env._run(coro)

    def goto(self, url: str, *, note: str = "") -> dict:
        before = self.page.url
        obs = self.injector.goto(url)
        obs_dict = obs if isinstance(obs, dict) else {
            "url": getattr(obs, "url", url),
            "page_content": getattr(obs, "ax_tree_text", "")}
        content = str(obs_dict.get("page_content") or "")
        fault_page = ("500 Internal Server Error" in content
                      or "503 Service Unavailable" in content)
        self.rec.action("goto", {"url": url}, url_before=before,
                        url_after=obs_dict.get("url") or self.page.url,
                        note=note,
                        extra={"proxy_step": self.injector._step_counter,
                               "fault_page": fault_page,
                               "observation_chars": len(content)})
        return obs_dict

    def eval_all(self, selector: str, js: str):
        return self._run(self.page.eval_on_selector_all(selector, js))

    def text(self, selector: str = "body") -> str:
        return self._run(self.page.inner_text(selector))

    def content(self) -> str:
        return self._run(self.page.content())

    def wait(self, ms: int = 300) -> None:
        self._run(self.page.wait_for_timeout(ms))

    def snapshot(self, name: str) -> None:
        self.rec.snapshot(self._run(self.page.inner_text("body")),
                          self._run(self.page.content()),
                          name=name, url=self.page.url)


# ---------------------------------------------------------------------------
# extractors
# ---------------------------------------------------------------------------

def _articles(actor) -> list[dict]:
    return actor.eval_all(
        "article",
        """els => els.map(a => {
            const title = a.querySelector('.submission__title, h1, h2');
            const link = a.querySelector('a.submission__link, h1 a, h2 a');
            const user = a.querySelector('.submission__submitter, .submission__info a[href^="/user/"]');
            const time = a.querySelector('time');
            return {
                title: title ? title.innerText.replace(/\\s+/g, ' ').trim() : '',
                href: link ? link.href : null,
                username: user ? user.innerText.trim() : null,
                time: time ? time.getAttribute('datetime') : null,
            };
        }).filter(x => x.title && x.href)""")


def _reddit_normal(forum: str):
    def run(actor) -> tuple[object, dict, list[str]]:
        actor.goto(f"{REDDIT}/f/{forum}/new",
                   note="newest listing on the task-critical forum endpoint")
        actor.snapshot(f"{forum}_new")
        articles = _articles(actor)
        if not articles:
            raise RuntimeError(f"no articles in /f/{forum}/new")
        first = articles[0]
        if "localhost:9999" not in (first["href"] or ""):
            raise RuntimeError(f"first post is not a local submission: {first}")
        actor.goto(first["href"], note="open the most recent self post")
        actor.snapshot(f"{forum}_post")
        title = actor.text(".submission__title").strip()
        author = actor.text(".submission__submitter").strip()
        comments = actor.eval_all(
            ".comment",
            """els => els.map(c => {
                const a = c.querySelector('.comment__header a[href^="/user/"]');
                const score = c.querySelector('.vote__net-score');
                const text = c.querySelector('.comment__body');
                return {
                    author: a ? a.innerText.trim() : null,
                    score: score ? score.innerText.trim() : null,
                    body: text ? text.innerText.replace(/\\s+/g, ' ').trim() : '',
                };
            })""")
        count = 0
        for comment in comments:
            if not comment["author"] or comment["author"] == author:
                continue
            raw = str(comment["score"] or "0").replace(",", "")
            try:
                value = int(re.match(r"-?\d+", raw).group(0))
            except (AttributeError, TypeError):
                value = 0
            if value < 0:
                count += 1
        answer = [{"username": author, "post_title": title, "count": count}]
        evidence = {
            "listing_url": f"{REDDIT}/f/{forum}/new",
            "post_url": actor.url,
            "article_count": len(articles),
            "selected": first,
            "comments_seen": len(comments),
            "comment_rule": ("exclude the post author; count comments with a negative "
                             "net score"),
            "negative_comment_authors": [c["author"] for c in comments
                                         if c["author"] != author
                                         and str(c["score"] or "").startswith("-")],
        }
        return answer, evidence, ["Most recent post is the first item on the /new listing."]
    return run


def _books_filter(actor, *, criterion: str) -> tuple[list[dict], list[dict]]:
    actor.goto(f"{REDDIT}/f/books", note="top-10 hot listing on /f/books")
    actor.snapshot("books_hot")
    articles = _articles(actor)[:10]
    keep = []
    for article in articles:
        title = article["title"]
        if criterion == "one_book":
            if re.search(r"(finished reading|audiobook of|just read|just finished)", title,
                         re.IGNORECASE):
                keep.append(article)
        elif criterion == "local_bookstores":
            if article["href"] and "localhost:9999" not in article["href"]:
                continue
            if ("bookshop.org" in title.casefold()
                    or "bookstore" in title.casefold()
                    or "book store" in title.casefold()):
                keep.append(article)
    return articles, keep


def _reddit_books_one(actor) -> tuple[object, dict, list[str]]:
    articles, keep = _books_filter(actor, criterion="one_book")
    if not keep:
        raise RuntimeError("no top-10 posts recommend exactly one book")
    evidence = {
        "listing_url": f"{REDDIT}/f/books",
        "top10": articles,
        "selected": keep,
        "selection_rule": ("title states a single reading/audiobook recommendation "
                           "(finished reading / audiobook of)"),
    }
    return [a["title"] for a in keep], evidence, [
        "Only the top 10 hot posts are read; each selected post names one book."]


def _reddit_books_local(actor) -> tuple[object, dict, list[str]]:
    articles, keep = _books_filter(actor, criterion="local_bookstores")
    if not keep:
        raise RuntimeError("no top-10 post talks about supporting local bookstores")
    domains = []
    details = []
    for article in keep:
        actor.goto(article["href"], note="open the local-bookstore post")
        actor.snapshot("local_bookstore_post")
        title = actor.text(".submission__title").strip()
        body = actor.text(".submission__body").strip()
        candidates = re.findall(
            r"\b(?:[a-z0-9-]+\.)+(?:com|org|net|io|co|us|store|bookshop)\b",
            f"{title} {body}", re.IGNORECASE)
        for value in candidates:
            value = value.strip(".,;:()[]").lower()
            if value not in domains:
                domains.append(value)
        details.append({"title": title, "url": actor.url, "domains": candidates})
    if not domains:
        raise RuntimeError("local-bookstore post has no organization URL")
    evidence = {
        "listing_url": f"{REDDIT}/f/books",
        "top10": articles,
        "selected_posts": keep,
        "post_details": details,
        "selection_rule": ("title/description mentions bookshop.org or supporting local "
                           "bookstores; organization domain taken verbatim from the post"),
    }
    return domains, evidence, ["Organization domains are read from the selected post."]


def _review_titles(product_id: int):
    def run(actor) -> tuple[object, dict, list[str]]:
        url = (f"{SHOPPING}/review/product/listAjax/id/{product_id}/")
        actor.goto(url, note="all customer reviews for the current product")
        actor.snapshot("reviews")
        reviews = actor.eval_all(
            ".review-item",
            """els => els.map(e => {
                const title = e.querySelector('.review-title');
                const rating = e.querySelector('.review-ratings');
                const author = e.querySelector('.review-author');
                const content = e.querySelector('.review-content');
                return {
                    title: title ? title.innerText.trim() : '',
                    rating: rating ? rating.innerText.trim() : '',
                    author: author ? author.innerText.replace(/^Review by\\s*/i, '').trim() : '',
                    content: content ? content.innerText.replace(/\\s+/g, ' ').trim() : '',
                };
            })""")
        matched = []
        for review in reviews:
            m = re.search(r"(\d+)%", review["rating"])
            if not m:
                continue
            # WebArena review ratings are percentages of five stars.
            if int(m.group(1)) <= 40:
                matched.append(review)
        answer = [r["title"] for r in matched]
        evidence = {
            "review_url": url,
            "reviews_seen": len(reviews),
            "matched": matched,
            "selection_rule": "displayed rating <= 40% (two stars or below)",
        }
        return answer, evidence, ["Review titles are taken verbatim from the review list."]
    return run


def _order_rows(actor, *, max_pages: int = 4) -> tuple[list[dict], list[dict]]:
    rows = []
    pages = []
    for page in range(1, max_pages + 1):
        url = f"{SHOPPING}/sales/order/history/" + (f"?p={page}" if page > 1 else "")
        actor.goto(url, note=f"order history page {page}")
        raw = actor.eval_all(
            "#my-orders-table tbody tr",
            """els => els.map(tr => {
                const cells = [...tr.querySelectorAll('td')].map(td => td.innerText.trim());
                const link = tr.querySelector('a');
                return {cells: cells, href: link ? link.href : null,
                        text: tr.innerText.replace(/\\s+/g, ' ').trim()};
            })""")
        if not raw:
            break
        pages.append({"page": page, "url": url, "rows": len(raw)})
        for row in raw:
            text = row["text"]
            m = re.match(r"(000000\d+)\s+(\d{1,2}/\d{1,2}/\d{2,4})\s+\$([\d,]+\.\d{2})\s+"
                         r"(Canceled|Complete|Pending|Processing|Closed)", text)
            if not m:
                continue
            rows.append({"order": m.group(1), "date": m.group(2),
                         "total": float(m.group(3).replace(",", "")),
                         "status": m.group(4), "href": row["href"], "text": text})
        # Stop when the paginator wraps back to the first page set.
        if page > 1 and raw and rows and all(r["order"] == rows[0]["order"]
                                             for r in rows[-len(raw):]):
            break
    return rows, pages


def _parse_order_date(value: str) -> str:
    month, day, year = value.split("/")
    year = int(year)
    year = 2000 + year if year < 100 else year
    return f"{year:04d}-{int(month):02d}-{int(day):02d}"


def _order_total(status_filter: str):
    def run(actor) -> tuple[object, dict, list[str]]:
        rows, pages = _order_rows(actor)
        matched = [r for r in rows if r["status"].casefold() == status_filter.casefold()]
        if not matched:
            raise RuntimeError(f"no {status_filter} order")
        selected = matched[0]
        answer = [selected["total"]]
        evidence = {"pages": pages, "rows": rows, "selected": selected,
                    "selection_rule": f"first (newest) order with status {status_filter}"}
        return answer, evidence, ["Order history is newest-first."]
    return run


def _order_aggregate(start: str, end: str):
    def run(actor) -> tuple[object, dict, list[str]]:
        rows, pages = _order_rows(actor)
        complete = []
        for row in rows:
            day = _parse_order_date(row["date"])
            if start <= day <= end and row["status"].casefold() == "complete":
                complete.append(row)
        answer = [{"order_count": len(complete),
                   "amount": round(sum(r["total"] for r in complete), 2)}]
        evidence = {"pages": pages, "rows": rows, "window": [start, end],
                    "complete_orders": complete,
                    "selection_rule": "status Complete and date inside the stated window"}
        return answer, evidence, ["Only complete orders inside the date window are summed."]
    return run


def _order_color(month: str, product_pattern: str):
    def run(actor) -> tuple[object, dict, list[str]]:
        rows, pages = _order_rows(actor)
        candidates = [r for r in rows
                      if _parse_order_date(r["date"]).startswith(month)
                      and r["status"].casefold() == "complete"]
        found = []
        for row in candidates:
            actor.goto(row["href"], note=f"open candidate order {row['order']}")
            actor.snapshot(f"order_{row['order']}")
            text = actor.text("body")
            # Product blocks are separated by SKU rows; search the block that
            # matches the requested product category.
            pattern = re.compile(product_pattern, re.IGNORECASE)
            for match in pattern.finditer(text):
                start = match.start()
                end = text.find("Subtotal", start)
                block = text[start:end if end > 0 else start + 1500]
                color = re.search(r"Color\s*(?:\||\n)\s*([^\t\n]+)", block)
                if color:
                    found.append({"order": row["order"], "product_match": match.group(0),
                                  "color": color.group(1).strip(), "url": actor.url})
        if not found:
            raise RuntimeError(f"no matching product color in {month} orders")
        selected = found[0]
        answer = [selected["color"]]
        evidence = {"pages": pages, "month": month, "candidates": candidates,
                    "matches": found, "selected": selected,
                    "selection_rule": ("complete order in the stated month; product name "
                                       "matches the category; Color option is the answer")}
        return answer, evidence, ["Color is read from the matching order line item."]
    return run


def _order_total_status(status_filter: str):
    # 188/190 only need the newest matching order; historical order page text
    # carries the total directly.
    return _order_total(status_filter)


def _search_range(brand: str):
    def run(actor) -> tuple[object, dict, list[str]]:
        actor.goto(f"{SHOPPING}/catalogsearch/result/?q={brand}",
                   note="brand search on the task-critical search endpoint")
        actor.snapshot("brand_search")
        items = []
        for page in range(1, 4):
            if page > 1:
                actor.goto(f"{SHOPPING}/catalogsearch/result/?q={brand}&p={page}",
                           note=f"brand search page {page}")
            found = actor.eval_all(
                "li.product-item",
                """els => els.map(e => {
                    const link = e.querySelector('.product-item-link');
                    const price = e.querySelector('[data-price-type="finalPrice"]');
                    return {
                        name: link ? link.innerText.replace(/\\s+/g, ' ').trim() : '',
                        price: price ? parseFloat(price.getAttribute('data-price-amount')) : null,
                    };
                }).filter(x => x.name && x.price !== null)""")
            items.extend(found)
            if not found:
                break
        matched = [i for i in items if brand.casefold() in i["name"].casefold()]
        if not matched:
            raise RuntimeError(f"no products from {brand}")
        answer = [{"min": min(i["price"] for i in matched),
                   "max": max(i["price"] for i in matched)}]
        evidence = {"brand": brand, "items_seen": len(items), "matched": matched,
                    "selection_rule": "product name contains the brand"}
        return answer, evidence, ["Min/max are taken over all brand matches on the search pages."]
    return run


def _review_names(actor) -> tuple[object, dict, list[str]]:
    actor.goto(f"{SHOPPING}/catalogsearch/result/?q=EYZUTAK+phone+case",
               note="case search on the task-critical search endpoint")
    actor.snapshot("case_search")
    links = actor.eval_all(
        "li.product-item a.product-item-link",
        """els => els.map(a => ({name: a.innerText.replace(/\\s+/g, ' ').trim(),
                               href: a.href})).filter(x => x.href)""")
    product = next((x for x in links if "eyzutak" in x["name"].casefold()
                    and "case" in x["name"].casefold()), None)
    if not product:
        raise RuntimeError("EYZUTAK phone case not found in search results")
    actor.goto(product["href"], note="open the EYZUTAK phone case")
    actor.snapshot("case_product")
    html = actor.content()
    m = re.search(r'data-product-id="(\d+)"', html)
    if not m:
        raise RuntimeError("product id missing on the case page")
    product_id = m.group(1)
    actor.goto(f"{SHOPPING}/review/product/listAjax/id/{product_id}/",
               note="load the case reviews")
    actor.snapshot("case_reviews")
    reviews = actor.eval_all(
        ".review-item",
        """els => els.map(e => {
            const author = e.querySelector('.review-author');
            const content = e.querySelector('.review-content');
            return {
                author: author ? author.innerText.replace(/^Review by\\s*/i, '').trim() : '',
                content: content ? content.innerText.replace(/\\s+/g, ' ').trim() : '',
            };
        })""")
    markers = ("broke", "broken", "peel", "peeling", "rub away", "rubbed away",
               "chip", "chipping", "loosen", "loosened", "defect", "damaged",
               "poor quality", "fell apart", "came apart", "stopped working")
    matched = [r for r in reviews
               if any(marker in r["content"].casefold() for marker in markers)]
    answer = [r["author"] for r in matched]
    evidence = {"product": product, "product_id": product_id,
                "reviews_seen": len(reviews), "matched": matched,
                "selection_rule": "review text states a durability/quality defect"}
    return answer, evidence, ["Customer names are taken from quality-complaint reviews."]


def _project_clone(name: str):
    def run(actor) -> tuple[object, dict, list[str]]:
        actor.goto(f"{GITLAB}/search?search={name}&nav_source=navbar",
                   note="project search on the task-critical GitLab search endpoint")
        actor.snapshot("project_search")
        links = actor.eval_all(
            'a[href^="/"]',
            """els => els.map(a => a.getAttribute('href'))
                .filter(h => h && h.split('/').length === 3 && !h.startsWith('/dashboard')
                             && !h.startsWith('/projects') && !h.startsWith('/groups')
                             && !h.startsWith('/users') && !h.startsWith('/-'))""")
        paths = []
        for href in links:
            if href not in paths:
                paths.append(href)
        target = next((p for p in paths if name.casefold() in p.casefold()), None)
        if not target:
            raise RuntimeError(f"no project path matching {name}: {paths}")
        actor.goto(f"{GITLAB}/api/v4/projects/{target.lstrip('/').replace('/', '%2F')}",
                   note="project identity and clone URL")
        data = json.loads(actor.text("body"))
        answer = [data["ssh_url_to_repo"]]
        evidence = {"search_url": f"{GITLAB}/search?search={name}",
                    "project_path": target, "project": data,
                    "selection_rule": "first search result path containing the requested name"}
        return answer, evidence, ["SSH clone URL is read from the project API object."]
    return run


def _graph(repo: str, branch: str, mode: str):
    def run(actor) -> tuple[object, dict, list[str]]:
        url = f"{GITLAB}/{repo}/-/graphs/{branch}"
        actor.goto(url, note="contributor graph on the task-critical endpoint")
        actor.wait(800)
        actor.snapshot("contributors")
        rows = actor.eval_all(
            ".col-lg-6",
            """els => els.map(e => {
                const h = e.querySelector('h4');
                const p = e.querySelector('p.gl-mb-3');
                return {name: h ? h.innerText.trim() : null,
                        line: p ? p.innerText.trim() : null};
            }).filter(x => x.name && x.line)""")
        parsed = []
        for row in rows:
            m = re.match(r"([\d,]+) commits? \(([^)]+)\)", row["line"])
            if not m:
                continue
            parsed.append({"name": row["name"], "commits": int(m.group(1).replace(",", "")),
                           "email": m.group(2)})
        if not parsed:
            raise RuntimeError(f"no contributors parsed from {url}")
        if mode == "names":
            answer = [r["name"] for r in parsed[:3]]
        elif mode == "emails":
            answer = [r["email"] for r in parsed[:3]]
        else:
            answer = [parsed[0]["email"]]
        evidence = {"url": url, "contributors": parsed, "mode": mode,
                    "selection_rule": "contributor graph order (commit count, merge commits excluded)"}
        return answer, evidence, ["The graph page already orders contributors by commit count."]
    return run


def _commits(repo: str, author: str, target: str):
    def run(actor) -> tuple[object, dict, list[str]]:
        url = f"{GITLAB}/{repo}/-/commits/main?author={author.replace(' ', '+')}"
        actor.goto(url, note="author-filtered commit history")
        actor.wait(500)
        actor.snapshot("commits")
        body = actor.text("body")
        groups = [(m.group(1), int(m.group(2))) for m in re.finditer(
            r"(\d{2} [A-Z][a-z]{2}, \d{4})\s+(\d+) commits?", body)]
        if target.endswith("2023") and target.startswith("Jan"):
            count = sum(n for day, n in groups if "Jan, 2023" in day)
        else:
            count = sum(n for day, n in groups if day == target)
        answer = [count]
        evidence = {"url": url, "author": author, "target": target,
                    "groups": groups, "selection_rule": "sum GitLab date groups for the target period"}
        return answer, evidence, ["Counts come from the author-filtered commit page."]
    return run


def _no_high_star_project(actor) -> tuple[object, dict, list[str]]:
    actor.goto(f"{GITLAB}/dashboard/projects", note="personal project list")
    actor.wait(500)
    actor.snapshot("dashboard_projects")
    rows = actor.eval_all(
        "li.project-row",
        """els => els.map(li => {
            const link = li.querySelector('h2 a') || li.querySelector('a.project');
            const stars = li.querySelector('a.stars');
            const role = li.querySelector('.user-access-role');
            return {href: link ? link.getAttribute('href') : null,
                    name: link ? link.innerText.replace(/\\s+/g, ' ').trim() : null,
                    stars: stars ? parseInt(stars.innerText.trim(), 10) : 0,
                    role: role ? role.innerText.trim() : null};
        }).filter(x => x.href)""")
    personal = [r for r in rows if (r["role"] or "").casefold() == "owner"]
    high = [r for r in personal if (r["stars"] or 0) > 100]
    evidence = {"personal_projects": personal, "high_star": high,
                "selection_rule": "role Owner; star count > 100"}
    if high:
        return [r["name"] for r in high], evidence, ["Projects above the threshold are returned."]
    return ("No matching result found: none of my personal projects has more than "
            "100 stars."), evidence, ["The personal project list was read in full."]


NORMAL = {
    27: _reddit_normal("personalfinance"),
    28: _reddit_normal("WorcesterMA"),
    48: _order_aggregate("2023-06-10", "2023-06-12"),
    49: _order_aggregate("2023-02-12", "2023-06-12"),
    66: _reddit_books_one,
    69: _reddit_books_local,
    148: _order_color("2022-09", "Framed Wall Art|Picture Frame"),
    149: _order_color("2023-02", "Artificial Plants"),
    163: _review_titles(38805),
    164: _review_titles(6523),
    168: _no_high_star_project,
    188: _order_total("Canceled"),
    190: _order_total("Complete"),
    206: _commits("a11yproject/a11yproject.com", "Eric Bailey", "02 Mar, 2023"),
    227: _search_range("EYZUTAK"),
    228: _search_range("sephora"),
    294: _project_clone("ChatGPT"),
    295: _project_clone("metaseq"),
    305: _commits("a11yproject/a11yproject.com", "Philip Wong", "Jan 2023"),
    314: _graph("primer/design", "main", "names"),
    316: _graph("facebook/create-react-app", "main", "emails"),
    384: _review_names,
    784: _graph("CellularPrivacy/Android-IMSI-Catcher-Detector", "master", "email"),
    785: _graph("CellularPrivacy/Android-IMSI-Catcher-Detector", "gh-pages", "email"),
}


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------

def _answer_text(task_id: int, answer) -> str:
    if isinstance(answer, str):
        return answer
    return json.dumps(answer, ensure_ascii=False)


def _score(task_id: int, out: Path, answer, *, completed: bool = True) -> dict:
    meta = task_meta(task_id)
    response = out / "agent_response.json"
    write_agent_response(response, meta["definition"], completed=completed,
                         answer=_answer_text(task_id, answer))
    har = out / "network.har"
    evaluation = evaluate_task_safe(
        task_id, agent_response_path=response,
        network_trace_path=har if har.is_file() else None,
        config_path=CONFIG)
    dump(out / "official_eval.json", evaluation)
    return evaluation


def run_normal(task_id: int, out: Path) -> dict:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    (out / "actions.jsonl").write_text("", encoding="utf-8")
    meta = task_meta(task_id)
    rec = Recorder(out)
    error = None
    answer = None
    evidence = {}
    notes = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=str(AUTH_DIR / f"{meta['site']}.json"),
            viewport={"width": 1400, "height": 1000},
            record_har_path=str(out / "network.har"))
        page = context.new_page()
        actor = PlaywrightActor(page, rec)
        try:
            actor.goto(meta["start_url"] or ENDPOINTS[task_id],
                       note="task start URL / shared login state")
            answer, evidence, notes = NORMAL[task_id](actor)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        finally:
            context.close()
            browser.close()
    evaluation = None
    if error is None:
        evaluation = _score(task_id, out, answer)
    summary = {
        "schema": "agent_stress_frozen_reference_path/1",
        "task_id": task_id,
        "arm": "normal",
        "recorded_at": now(),
        "site": meta["site"],
        "start_url": meta["start_url"],
        "intent": meta["intent"],
        "intent_template_id": meta["intent_template_id"],
        "endpoint": ENDPOINTS[task_id],
        "steps": len(rec.actions),
        "actions": rec.actions,
        "pages": rec.pages,
        "answer": answer,
        "evidence": evidence,
        "notes": notes,
        "error": error,
        "official_eval": evaluation,
        "official_success": bool((evaluation or {}).get("official_success")),
    }
    dump(out / "reference_path.json", summary)
    return summary


def run_recovery(task_id: int, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    (out / "actions.jsonl").write_text("", encoding="utf-8")
    meta = task_meta(task_id)
    rec = Recorder(out)
    endpoint = ServiceEndpoint.from_url(ENDPOINTS[task_id])
    config = FaultConfig.single_fault(
        "web_http_error", intensity="high", seed=1,
        experiment_id=EXPERIMENT_ID, fault_variant="persistent",
        fault_window_steps=WINDOW_STEPS, fault_target_endpoint=endpoint.spec())
    env = SyncBrowserEnv(
        headless=True, extra_http_headers=get_auto_login_headers(meta["site"]),
        storage_state=str(AUTH_DIR / f"{meta['site']}.json"),
        har_path=str(out / "network.har"))
    from fault_injection.injector import FaultInjector

    error = None
    answer = None
    evidence = {}
    notes = []
    steps = []
    env.start()
    try:
        injector = FaultInjector(env, config)
        actor = InjectActor(env, injector, rec)
        actor.goto(meta["start_url"] or ENDPOINTS[task_id],
                   note="bootstrap on the task start URL (not armed)")
        injector.arm_http_fault()
        trigger = actor.goto(TRIGGER_URLS[task_id],
                             note="first qualified navigation to the fault endpoint")
        steps.append({"label": "trigger", **{k: trigger.get(k) for k in ("url",)},
                      "fault_page": "500 Internal Server Error" in str(
                          trigger.get("page_content") or "")
                      or "503 Service Unavailable" in str(
                          trigger.get("page_content") or ""),
                      "proxy_step": injector._step_counter})
        fillers = FILLERS[meta["site"]]
        for index in range(WINDOW_STEPS - 1):
            url = fillers[index % len(fillers)]
            obs = actor.goto(url, note=f"window filler {index + 1}/{WINDOW_STEPS - 1}")
            content = str(obs.get("page_content") or "")
            steps.append({"label": f"filler_{index + 1}", "url": url,
                          "fault_page": ("500 Internal Server Error" in content
                                         or "503 Service Unavailable" in content),
                          "proxy_step": injector._step_counter})
        answer, evidence, notes = NORMAL[task_id](actor)
        steps.append({"label": "normal_after_window", "url": actor.url,
                      "proxy_step": injector._step_counter})
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    finally:
        episode = {
            "snapshot": config.episode.snapshot(),
            "injections": [e for e in config.episode.events
                           if e.get("event") == "injection"],
            "events": config.episode.events,
        }
        verdict = classify_service_episode(
            {"experiment_id": EXPERIMENT_ID, "variant": "persistent",
             "window_steps": WINDOW_STEPS, **episode},
            expected_variant="persistent", expected_window=WINDOW_STEPS)
        fault_evidence = {
            "schema": "agent_stress_frozen_fault_recovery/1",
            "task_id": task_id,
            "endpoint": endpoint.spec(),
            "variant": "persistent",
            "window_steps": WINDOW_STEPS,
            "steps": steps,
            "exposures": injector.get_exposure_log(),
            "episode": episode,
            "injection_log": injector.get_injection_log(),
            "verdict": verdict,
        }
        dump(out / "fault_evidence.json", fault_evidence)
        env.stop()
    evaluation = None
    if error is None:
        evaluation = _score(task_id, out, answer)
    summary = {
        "schema": "agent_stress_frozen_reference_path/1",
        "task_id": task_id,
        "arm": "recovery",
        "recorded_at": now(),
        "site": meta["site"],
        "endpoint": ENDPOINTS[task_id],
        "trigger_url": TRIGGER_URLS[task_id],
        "window_steps": WINDOW_STEPS,
        "steps": len(rec.actions),
        "actions": rec.actions,
        "pages": rec.pages,
        "answer": answer,
        "evidence": evidence,
        "notes": notes,
        "error": error,
        "fault_evidence": fault_evidence,
        "official_eval": evaluation,
        "official_success": bool((evaluation or {}).get("official_success")),
    }
    dump(out / "reference_path.json", summary)
    return summary


def run_task(task_id: int, root: Path) -> dict:
    task_dir = root / f"task{task_id}"
    normal = run_normal(task_id, task_dir / "normal")
    recovery = run_recovery(task_id, task_dir / "recovery")
    normal_ok = (normal["error"] is None and normal["official_success"]
                 and normal["steps"] <= MAX_STEPS)
    episode = (recovery.get("fault_evidence") or {}).get("episode", {})
    snapshot = episode.get("snapshot", {})
    injections = snapshot.get("injections") or 0
    exposure_steps = [e for e in (recovery.get("fault_evidence") or {}).get("steps", [])
                      if e.get("label") == "trigger"]
    trigger_fault_page = bool(exposure_steps and exposure_steps[0].get("fault_page"))
    released = snapshot.get("released")
    recovery_ok = (recovery["error"] is None and recovery["official_success"]
                   and recovery["steps"] <= MAX_STEPS and injections >= 1
                   and trigger_fault_page and bool(released))
    acceptance = {
        "schema": "agent_stress_frozen_acceptance/1",
        "task_id": task_id,
        "site": normal["site"],
        "template_id": normal["intent_template_id"],
        "endpoint": ENDPOINTS[task_id],
        "supplement": task_id in SUPPLEMENT,
        "fault_pilot": task_id in PILOT,
        "normal": {
            "passed": normal_ok,
            "steps": normal["steps"],
            "official_success": normal["official_success"],
            "answer": normal["answer"],
            "error": normal["error"],
            "evidence": f"task{task_id}/normal/reference_path.json",
        },
        "trigger": {
            "passed": injections >= 1 and trigger_fault_page,
            "injections": injections,
            "first_exposure_step": snapshot.get("t0"),
            "error_type": (episode.get("injections") or [{}])[0].get("error_type"),
            "evidence": f"task{task_id}/recovery/fault_evidence.json",
        },
        "recovery": {
            "passed": recovery_ok,
            "steps": recovery["steps"],
            "window_steps": WINDOW_STEPS,
            "released": released,
            "official_success": recovery["official_success"],
            "answer": recovery["answer"],
            "error": recovery["error"],
            "evidence": f"task{task_id}/recovery/reference_path.json",
        },
        "passed": bool(normal_ok and recovery_ok and injections >= 1
                       and trigger_fault_page),
    }
    dump(task_dir / "acceptance.json", acceptance)
    return acceptance


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=int, action="append", choices=ROSTER)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    root = args.output_dir
    root.mkdir(parents=True, exist_ok=True)
    tasks = args.task or ROSTER
    rows = []
    for task_id in tasks:
        existing = root / f"task{task_id}" / "acceptance.json"
        if args.resume and existing.is_file():
            rows.append(json.loads(existing.read_text(encoding="utf-8")))
            continue
        print(f"[reference] task {task_id} ...", flush=True)
        row = run_task(task_id, root)
        rows.append(row)
        print(f"[reference] task {task_id}: passed={row['passed']} "
              f"normal={row['normal']['passed']} recovery={row['recovery']['passed']} "
              f"inj={row['trigger']['injections']}", flush=True)
    passed = [r for r in rows if r["passed"]]
    summary = {
        "schema": "agent_stress_frozen_reference_summary/1",
        "created_at": now(),
        "roster": tasks,
        "passed": len(passed),
        "total": len(rows),
        "rows": rows,
    }
    dump(root / "acceptance.json", summary)
    print(json.dumps({"passed": summary["passed"], "total": summary["total"]},
                     ensure_ascii=False))
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
