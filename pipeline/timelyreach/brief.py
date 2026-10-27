"""每日简报: one Chinese morning brief per US trading day, built from the last
24 hours of finance and news posts already on the site."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import load_site
from .models import Article, RawItem, url_hash
from .rewrite import Provider, to_article
from .store import Store

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
SOURCE_BOARDS = ("picks", "finance", "news")
EARLIEST_HOUR_ET = 6  # write it once the overnight news is in


def _line(a: Article) -> str:
    """Title plus a short deck; the deck often starts with the title itself, so drop that part."""
    deck = a.summary.strip()
    if deck.startswith(a.title[:20]):
        deck = deck[len(a.title):].lstrip("：:，, ")
    return a.title + (f"：{deck[:90]}" if deck else "")


def brief_key(day: str) -> str:
    return url_hash(f"brief:{day}")


def run_brief(store: Store, providers: list[Provider], now: datetime | None = None) -> Article | None:
    now_et = (now or datetime.now(timezone.utc)).astimezone(ET)
    day = now_et.strftime("%Y-%m-%d")
    if now_et.weekday() >= 5 or now_et.hour < EARLIEST_HOUR_ET or store.is_seen(brief_key(day)):
        return None

    since = (now_et - timedelta(hours=24)).astimezone(timezone.utc).isoformat(timespec="seconds")
    recent = [a for a in store.load_articles() if a.board in SOURCE_BOARDS and a.created >= since]
    # owner's picks first, then finance, then general news; newest first within each
    recent.sort(key=lambda a: a.created, reverse=True)
    recent.sort(key=lambda a: SOURCE_BOARDS.index(a.board))
    recent = recent[:15]
    if len(recent) < 3:
        log.info("brief skipped: only %d recent articles", len(recent))
        return None

    date_cn = f"{now_et.month}月{now_et.day}日"
    lines = [f"{i}. {_line(a)}" for i, a in enumerate(recent, 1)]
    item = RawItem(
        source_id="brief",
        source_name="本站编辑部",
        lang="brief",
        board="brief",
        url=f"{load_site().base_url}/f/brief/#{day}",
        title=f"{date_cn}财经早报：{recent[0].title}",
        summary=f"{date_cn}（美东时间）开盘前，一帖看懂过去 24 小时的财经要闻。",
        text="\n".join(lines),
    )
    article = to_article(item, providers)
    if article is None:
        return None
    article.key = brief_key(day)
    store.save_article(article)
    log.info("[brief] %s", article.title)
    return article
