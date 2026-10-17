"""JSON-file storage under data/ (committed back to the repo by the workflow)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import DATA_DIR
from .models import Article


class Store:
    def __init__(self, root: Path = DATA_DIR) -> None:
        self.root = root
        self.articles_dir = root / "articles"
        self.seen_path = root / "seen.json"
        self.seen: dict[str, str] = {}
        if self.seen_path.exists():
            self.seen = json.loads(self.seen_path.read_text(encoding="utf-8"))

    def is_seen(self, key: str) -> bool:
        return key in self.seen

    def mark_seen(self, key: str) -> None:
        self.seen[key] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def save_article(self, article: Article) -> Path:
        path = self.articles_dir / f"{article.slug}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(article.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        self.mark_seen(article.key)
        return path

    def flush(self, keep_days: int = 120) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
        self.seen = {k: v for k, v in self.seen.items() if v >= cutoff}
        self.root.mkdir(parents=True, exist_ok=True)
        self.seen_path.write_text(json.dumps(self.seen, indent=0, sort_keys=True), encoding="utf-8")

    def load_articles(self) -> list[Article]:
        if not self.articles_dir.exists():
            return []
        articles = [
            Article.from_dict(json.loads(p.read_text(encoding="utf-8")))
            for p in self.articles_dir.rglob("*.json")
        ]
        articles.sort(key=lambda a: a.created, reverse=True)
        return articles
