"""Articles dropped into inbox/ as JSON (by the admin page or an outside agent) are published as-is.

The writer has already rewritten them, so no LLM is involved. Processed files move to
inbox/done/, malformed ones to inbox/rejected/ so they do not block the next run.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .config import REPO_DIR, load_site
from .models import Article, url_hash
from .store import Store

log = logging.getLogger(__name__)

INBOX_DIR = REPO_DIR / "inbox"


def _iso(value: str) -> str:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_inbox_file(path: Path) -> Article:
    d = json.loads(path.read_text(encoding="utf-8"))
    title = str(d.get("title", "")).strip()
    body = [str(p).strip() for p in d.get("body", []) if str(p).strip()]
    if not title or not body:
        raise ValueError("title and body are required")
    board = str(d.get("board") or "finance")
    site = load_site()
    if board not in site.board_map or site.board_map[board].kind != "auto":
        board = "finance"
    url = str(d.get("source_url", "")).strip()
    created = _iso(d.get("created", ""))
    owner = d.get("author") == "站长"
    return Article(
        key=url_hash(url or f"inbox:{title}:{created}"),
        board=board,
        source_id="editor" if owner else "inbox",
        source_name=str(d.get("source_name") or ("站长" if owner else "综合媒体")).strip(),
        source_url=url,
        title=title,
        summary=str(d.get("summary", "")).strip() or body[0][:80],
        body=body,
        tags=[str(t).strip() for t in d.get("tags", []) if str(t).strip()][:6],
        lang="zh",
        created=created,
        rewriter="inbox",
        show_link=bool(url),
    )


def run_inbox(store: Store, inbox: Path | None = None) -> int:
    inbox = inbox or INBOX_DIR
    if not inbox.exists():
        return 0
    created = 0
    for path in sorted(inbox.glob("*.json")):
        try:
            article = parse_inbox_file(path)
        except Exception as exc:  # noqa: BLE001 - one bad file must not block the rest
            log.warning("inbox file %s rejected: %s", path.name, exc)
            dest = inbox / "rejected"
        else:
            if not store.is_seen(article.key):
                store.save_article(article)
                created += 1
                log.info("[inbox] %s", article.title)
            dest = inbox / "done"
        dest.mkdir(exist_ok=True)
        shutil.move(str(path), dest / path.name)
    return created
