"""One content run: fetch every active source, rewrite new items, store them."""
from __future__ import annotations

import logging
import os
import re

from .config import load_sources
from .fetchers import fetch_article, fetch_source
from .inbox import run_inbox
from .brief import run_brief
from .picks import run_picks
from .rewrite import available_providers, to_article
from .store import Store

log = logging.getLogger(__name__)

# video/podcast pages have no article text worth compiling
SKIP_URL = re.compile(r"/(videos?|audio|podcasts?|live-blog)/", re.I)


def credit_reposted_sources(store: Store, limit: int = 60) -> int:
    """Older posts were credited to 文学城, which only reposts; look up the outlet named on
    each page and credit that instead. Runs a batch per update until none are left."""
    fixed = 0
    for a in store.load_articles():
        if fixed >= limit:
            break
        if a.source_id != "wenxuecity" or a.source_name != "文学城":
            continue
        _, outlet = fetch_article(a.source_url)
        outlet = outlet or "综合媒体"
        a.body = [p.replace("文学城", outlet) for p in a.body]
        a.tags = [outlet if t == "文学城" else t for t in a.tags]
        a.source_name, a.show_link = outlet, False
        store.save_article(a)
        fixed += 1
    if fixed:
        log.info("credited %d reposted articles to their original outlets", fixed)
    return fixed


def run_update(store: Store | None = None, max_new: int | None = None) -> int:
    store = store or Store()
    max_new = max_new if max_new is not None else int(os.environ.get("TR_MAX_NEW", "40"))
    created = run_inbox(store)  # already written by the owner or an outside agent; no LLM needed
    providers = available_providers()
    log.info("rewrite providers: %s", [p.name for p in providers])
    if not providers:
        log.error("no rewrite provider available; nothing to do")
        store.flush()
        return created

    created += run_picks(store, providers)
    for source in load_sources():
        if not source.is_active():
            log.info("source %s skipped (disabled or missing %s)", source.id, source.requires_env)
            continue
        fresh = [
            it for it in fetch_source(source)
            if not store.is_seen(it.key) and not SKIP_URL.search(it.url)
        ][: source.limit]
        for item in fresh:
            if created >= max_new:
                break
            if source.full_text:
                item.text, outlet = fetch_article(item.url)
                if outlet:  # reposting sites: credit the outlet named on the page
                    item.source_name = outlet
            article = to_article(item, providers)
            if article is None:
                log.warning("could not rewrite %s", item.url)
                continue
            store.save_article(article)
            created += 1
            log.info("[%s] %s  <-  %s", article.rewriter, article.title, item.title)
    credit_reposted_sources(store)
    if run_brief(store, providers):  # after this run's news, so the brief includes it
        created += 1
    store.flush()
    log.info("created %d new articles", created)
    return created
