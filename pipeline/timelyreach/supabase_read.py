"""Read public reader threads from Supabase at build time, so they get static,
crawlable pages (/p/<id>.html) and sitemap entries. Uses only the public anon
key; row-level security decides what is visible."""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)


def supabase_settings() -> tuple[str, str]:
    return os.environ.get("SUPABASE_URL", "").rstrip("/"), os.environ.get("SUPABASE_ANON_KEY", "")


def load_view_stats(limit: int = 500) -> dict[str, tuple[int, int]]:
    """thread_key -> (views, replies) for the most viewed threads. Empty when Supabase is not set up."""
    url, key = supabase_settings()
    if not url or not key:
        return {}
    try:
        resp = requests.get(
            f"{url}/rest/v1/thread_stats",
            params={"select": "thread_key,view_count,reply_count", "order": "view_count.desc", "limit": str(limit)},
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=30,
        )
        resp.raise_for_status()
        return {r["thread_key"]: (r.get("view_count") or 0, r.get("reply_count") or 0) for r in resp.json()}
    except Exception as exc:  # noqa: BLE001 - without stats the lists are simply in time order
        log.warning("could not load view stats from supabase: %s", exc)
        return {}


def load_user_threads(limit: int = 2000) -> list[dict]:
    url, key = supabase_settings()
    if not url or not key:
        return []
    try:
        resp = requests.get(
            f"{url}/rest/v1/threads_public",
            params={
                "select": "id,board,title,body,created_at,updated_at,anonymous,author_username,author_display_name",
                "order": "created_at.desc",
                "limit": str(limit),
            },
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=30,
        )
        resp.raise_for_status()
        threads = resp.json()
    except Exception as exc:  # noqa: BLE001 - the site still builds without reader threads
        log.warning("could not load reader threads from supabase: %s", exc)
        return []
    for t in threads:
        # anonymous posts come back without author columns
        t["author"] = (
            {"username": t["author_username"], "display_name": t.get("author_display_name")}
            if t.get("author_username") else None
        )
    return threads

