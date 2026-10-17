"""站长荐读: turn the site owner's GitHub issues (link + own commentary) into posts.

The owner opens an issue with the "站长荐读" form. On the next run the robot
writes a post around the owner's commentary, replies on the issue with the
post link and closes it. Issues from anyone else are ignored, because the
repository is public.
"""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .config import load_site
from .fetchers import clean_text, http_get
from .models import RawItem, url_hash
from .rewrite import Provider, to_article
from .store import Store

log = logging.getLogger(__name__)

API = "https://api.github.com"
PICK_LABEL = "pick"
TITLE_PREFIX = "[荐读]"
SITE_NAMES = {
    "wsj.com": "华尔街日报",
    "bloomberg.com": "彭博社",
    "nypost.com": "纽约邮报",
    "zerohedge.com": "ZeroHedge",
    "ft.com": "金融时报",
    "nytimes.com": "纽约时报",
    "reuters.com": "路透社",
    "cnbc.com": "CNBC",
    "x.com": "推特",
    "twitter.com": "推特",
}


def parse_issue_form(body: str) -> dict[str, str]:
    """Issue forms render as '### 标签\\n\\n内容' sections."""
    fields: dict[str, str] = {}
    for block in re.split(r"^###\s+", body or "", flags=re.M)[1:]:
        label, _, value = block.partition("\n")
        value = value.strip()
        fields[label.strip()] = "" if value == "_No response_" else value
    return fields


def site_name(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    for domain, name in SITE_NAMES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host or "原文"


def page_meta(url: str) -> tuple[str, str]:
    """Public title/description meta of the linked page (works on most paywalled sites)."""
    try:
        soup = BeautifulSoup(http_get(url).text, "html.parser")
    except Exception as exc:  # noqa: BLE001 - the owner's notes are enough without it
        log.info("meta fetch failed for %s: %s", url, exc)
        return "", ""

    def meta(*names: str) -> str:
        for n in names:
            tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if tag and tag.get("content"):
                return clean_text(tag["content"])
        return ""

    title = meta("og:title", "twitter:title") or clean_text(soup.title.string if soup.title else "")
    return title, meta("og:description", "description", "twitter:description")


class GitHubIssues:
    def __init__(self, repo: str, token: str) -> None:
        self.repo = repo
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})

    def open_issues(self) -> list[dict]:
        resp = self.session.get(f"{API}/repos/{self.repo}/issues", params={"state": "open", "per_page": 50}, timeout=30)
        resp.raise_for_status()
        return [i for i in resp.json() if "pull_request" not in i]

    def reply_and_close(self, number: int, text: str) -> None:
        self.session.post(f"{API}/repos/{self.repo}/issues/{number}/comments", json={"body": text}, timeout=30).raise_for_status()
        self.session.patch(f"{API}/repos/{self.repo}/issues/{number}", json={"state": "closed", "state_reason": "completed"}, timeout=30).raise_for_status()


def is_pick(issue: dict, owner: str) -> bool:
    by_owner = (issue.get("user") or {}).get("login", "").lower() == owner.lower()
    labelled = any(lbl.get("name") == PICK_LABEL for lbl in issue.get("labels", []))
    return by_owner and (labelled or issue.get("title", "").startswith(TITLE_PREFIX))


def issue_to_item(issue: dict) -> RawItem | None:
    fields = parse_issue_form(issue.get("body", ""))
    url_match = re.search(r"https?://\S+", fields.get("原文链接", "") or issue.get("body", ""))
    notes = fields.get("我的点评", "").strip()
    if not url_match or not notes:
        return None
    url = url_match.group(0).rstrip(").,，。")
    meta_title, meta_desc = page_meta(url)
    excerpt = fields.get("原文摘录（可选，最多两句）", "").strip()
    title = issue.get("title", "").removeprefix(TITLE_PREFIX).strip() or meta_title
    text = notes + (f"\n原文摘录：「{excerpt}」" if excerpt else "")
    summary = "；".join(x for x in [f"原文标题：{meta_title}" if meta_title else "", f"原文摘要：{meta_desc}" if meta_desc else ""] if x)
    board = fields.get("发到哪个版块", "").split(" ")[0] or "picks"
    return RawItem(
        source_id="editor",
        source_name=site_name(url),
        lang="editor",
        board=board,
        url=url,
        title=title or meta_title or url,
        summary=summary,
        text=text,
        author="站长",
    )


def run_picks(store: Store, providers: list[Provider]) -> int:
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        log.info("picks skipped: GITHUB_REPOSITORY / GITHUB_TOKEN not set")
        return 0
    site = load_site()
    gh = GitHubIssues(repo, token)
    try:
        issues = gh.open_issues()
    except Exception as exc:  # noqa: BLE001
        log.warning("could not list issues: %s", exc)
        return 0

    created = 0
    for issue in issues:
        if not is_pick(issue, site.admin_github):
            continue
        key = url_hash(f"issue:{repo}#{issue['number']}")  # one post per issue, even if edited
        if store.is_seen(key):
            continue
        item = issue_to_item(issue)
        if item is None:
            log.warning("issue #%s has no link or commentary; skipped", issue["number"])
            continue
        if item.board not in site.board_map or site.board_map[item.board].kind != "auto":
            item.board = "picks"
        article = to_article(item, providers)
        if article is None:
            continue
        article.key = key
        store.save_article(article)
        created += 1
        link = f"{site.base_url}/t/{article.slug}.html"
        try:
            gh.reply_and_close(issue["number"], f"已发布：[{article.title}]({link})\n\n（网站几分钟后更新）")
        except Exception as exc:  # noqa: BLE001
            log.warning("could not reply to issue #%s: %s", issue["number"], exc)
        log.info("[pick] %s", article.title)
    return created
