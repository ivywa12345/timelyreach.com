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


def load_user_threads(limit: int = 2000) -> list[dict]:
    url, key = supabase_settings()
    if not url or not key:
        return []
    try:
        resp = requests.get(
            f"{url}/rest/v1/threads",
            params={
                "select": "id,board,title,body,created_at,updated_at,author:profiles(username,display_name)",
                "deleted": "eq.false",
                "order": "created_at.desc",
                "limit": str(limit),
            },
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001 - the site still builds without reader threads
        log.warning("could not load reader threads from supabase: %s", exc)
        return []
