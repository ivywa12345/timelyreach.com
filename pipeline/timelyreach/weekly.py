"""本周回顾: one Chinese review per week, published on the weekend when there is no daily brief.
Built from the most-read (or newest) pieces of the last seven days plus the week's brief headlines."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .brief import ET
from .config import load_site
from .models import Article, RawItem, url_hash
from .rewrite import Provider, to_article
from .store import Store
from .supabase_read import load_view_stats

log = logging.getLogger(__name__)

SOURCE_BOARDS = ("picks", "finance", "news")
EARLIEST_HOUR_ET = 9  # Saturday morning, after the Friday-night news has settled
PICKS = 10


def weekly_key(monday: str) -> str:
    return url_hash(f"weekly:{monday}")


def _line(a: Article) -> str:
    deck = a.summary.strip()
    if deck.startswith(a.title[:20]):
        deck = deck[len(a.title):].lstrip("：:，, ")
    return a.title + (f"：{deck[:90]}" if deck else "")


def run_weekly(store: Store, providers: list[Provider], now: datetime | None = None,
               stats: dict[str, tuple[int, int]] | None = None) -> Article | None:
    now_et = (now or datetime.now(timezone.utc)).astimezone(ET)
    monday = (now_et - timedelta(days=now_et.weekday())).strftime("%Y-%m-%d")
    if now_et.weekday() < 5 or now_et.hour < EARLIEST_HOUR_ET or store.is_seen(weekly_key(monday)):
        return None  # weekdays have the daily brief; one review per week

    since = (now_et - timedelta(days=7)).astimezone(timezone.utc).isoformat(timespec="seconds")
    week = [a for a in store.load_articles() if a.created >= since]
    candidates = [a for a in week if a.board in SOURCE_BOARDS]
    briefs = sorted((a for a in week if a.board == "brief" and a.created >= since), key=lambda a: a.created)
    if len(candidates) < 5:
        log.info("weekly review skipped: only %d articles this week", len(candidates))
        return None

    stats = load_view_stats() if stats is None else stats

    def rank(a: Article):  # most read first; the owner's picks and newest break ties
        views, replies = stats.get(a.thread_key, (0, 0))
        return (views + 5 * replies, a.source_id == "editor", a.created)

    chosen = sorted(sorted(candidates, key=rank, reverse=True)[:PICKS], key=lambda a: a.created)
    first = (now_et - timedelta(days=now_et.weekday())).date()
    last = first + timedelta(days=4)
    span = f"{first.month}月{first.day}日–{last.month}月{last.day}日"
    lead = max(chosen, key=rank)
    text = "\n".join(
        [f"{i}. {_line(a)}" for i, a in enumerate(chosen, 1)]
        + (["本周每日早报标题："] + [f"- {b.title}" for b in briefs[-5:]] if briefs else [])
    )
    item = RawItem(
        source_id="brief",
        source_name="本站编辑部",
        lang="weekly",
        board="brief",
        url=f"{load_site().base_url}/f/brief/#weekly-{monday}",
        title=f"本周回顾（{span}）：{lead.title}",
        summary=f"{span}这一周，美股和美国新闻里最值得回头看的几件事。",
        text=text,
    )
    article = to_article(item, providers)
    if article is None:
        return None
    article.key = weekly_key(monday)
    store.save_article(article)
    log.info("[weekly] %s", article.title)
    return article
