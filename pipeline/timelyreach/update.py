"""One content run: fetch every active source, rewrite new items, store them."""
from __future__ import annotations

import logging
import os

from .config import load_sources
from .fetchers import fetch_full_text, fetch_source
from .picks import run_picks
from .rewrite import available_providers, to_article
from .store import Store

log = logging.getLogger(__name__)


def run_update(store: Store | None = None, max_new: int | None = None) -> int:
    store = store or Store()
    max_new = max_new if max_new is not None else int(os.environ.get("TR_MAX_NEW", "40"))
    providers = available_providers()
    log.info("rewrite providers: %s", [p.name for p in providers])
    if not providers:
        log.error("no rewrite provider available; nothing to do")
        return 0

    created = run_picks(store, providers)
    for source in load_sources():
        if not source.is_active():
            log.info("source %s skipped (disabled or missing %s)", source.id, source.requires_env)
            continue
        fresh = [it for it in fetch_source(source) if not store.is_seen(it.key)][: source.limit]
        for item in fresh:
            if created >= max_new:
                break
            if source.full_text:
                item.text = fetch_full_text(item.url)
            article = to_article(item, providers)
            if article is None:
                log.warning("could not rewrite %s", item.url)
                continue
            store.save_article(article)
            created += 1
            log.info("[%s] %s  <-  %s", article.rewriter, article.title, item.title)
    store.flush()
    log.info("created %d new articles", created)
    return created
