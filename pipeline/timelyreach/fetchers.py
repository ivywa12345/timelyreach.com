"""Fetch items from RSS feeds, RSSHub routes and forum list pages.

Every network failure is logged and swallowed: one broken source must never
stop the daily run.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

from .config import Source
from .models import RawItem

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
TIMEOUT = 25
RSSHUB_BASE = os.environ.get("RSSHUB_BASE", "http://localhost:1200").rstrip("/")

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8"})


def http_get(url: str) -> requests.Response:
    resp = _session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp


def clean_text(html_or_text: str) -> str:
    if not html_or_text:
        return ""
    text = BeautifulSoup(html_or_text, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _iso(struct_time) -> str:
    if not struct_time:
        return ""
    return datetime(*struct_time[:6], tzinfo=timezone.utc).isoformat(timespec="seconds")


def parse_feed(content: bytes, source: Source) -> list[RawItem]:
    feed = feedparser.parse(content)
    items = []
    for e in feed.entries:
        url = e.get("link") or ""
        title = clean_text(e.get("title", ""))
        if not url or not title:
            continue
        summary_html = e.get("summary", "") or ""
        if not summary_html and e.get("content"):
            summary_html = e["content"][0].get("value", "")
        items.append(
            RawItem(
                source_id=source.id,
                source_name=source.name,
                lang=source.lang,
                board=source.board,
                url=url,
                title=title,
                summary=clean_text(summary_html)[:3000],
                author=clean_text(e.get("author", "")),
                published=_iso(e.get("published_parsed") or e.get("updated_parsed")),
            )
        )
    return items


# Link texts that are timestamps or counters, e.g. "10 分钟前", "2026-10-17", "3 replies"
JUNK_TITLE = re.compile(r"^(\d+\s*(秒|分钟|小时|天|周|个月|年)前|昨天.*|前天.*|[\d\s:/.-]+|\d+\s*[A-Za-z]{0,8})$")


def parse_links(html: str, base_url: str, pattern: str, source: Source) -> list[RawItem]:
    """Pick thread links off a forum list page by URL pattern, in page order."""
    soup = BeautifulSoup(html, "html.parser")
    rx = re.compile(pattern)
    seen: set[str] = set()
    items = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not rx.search(href) or "#" in href:  # "#p123" links jump to a reply, not a thread
            continue
        title = clean_text(a.get_text(" ", strip=True)) or clean_text(a.get("title", ""))
        if len(title) < 6 or JUNK_TITLE.match(title):  # pagination, "最后一页", "10 分钟前"
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        items.append(
            RawItem(
                source_id=source.id,
                source_name=source.name,
                lang=source.lang,
                board=source.board,
                url=url,
                title=title[:200],
            )
        )
    return items


def _fetch_one(spec: dict, source: Source) -> list[RawItem]:
    kind = spec.get("type")
    if kind == "rss":
        return parse_feed(http_get(spec["url"]).content, source)
    if kind == "rsshub":
        return parse_feed(http_get(RSSHUB_BASE + spec["route"]).content, source)
    if kind == "links":
        resp = http_get(spec["url"])
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding
        return parse_links(resp.text, spec["url"], spec["pattern"], source)
    raise ValueError(f"unknown fetch type {kind!r}")


def fetch_source(source: Source) -> list[RawItem]:
    """Try the source's fetch specs in order; with merge=True combine all of them."""
    collected: list[RawItem] = []
    for spec in source.fetch:
        try:
            items = _fetch_one(spec, source)
        except Exception as exc:  # noqa: BLE001 - any failure just moves to the next spec
            log.warning("source %s: %s failed: %s", source.id, spec, exc)
            continue
        log.info("source %s: %d items from %s", source.id, len(items), spec.get("url") or spec.get("route"))
        if not items:
            continue
        if source.merge:
            collected.extend(items[: source.limit])
        else:
            return items
    return collected


def fetch_full_text(url: str) -> str:
    """Best-effort article body extraction (trafilatura). Empty string on failure."""
    try:
        import trafilatura

        resp = http_get(url)
        text = trafilatura.extract(resp.content, include_comments=False, include_tables=False, url=url)
        return (text or "")[:12000]
    except Exception as exc:  # noqa: BLE001
        log.info("full text failed for %s: %s", url, exc)
        return ""
