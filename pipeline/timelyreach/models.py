"""Data records that flow through fetch -> rewrite -> store -> build."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def url_hash(url: str) -> str:
    return hashlib.sha1(url.strip().encode("utf-8")).hexdigest()[:10]


@dataclass
class RawItem:
    """An item as it comes out of a source, before any rewriting."""

    source_id: str
    source_name: str
    lang: str
    board: str
    url: str
    title: str
    summary: str = ""
    text: str = ""
    author: str = ""
    published: str = ""  # ISO-8601 UTC

    @property
    def key(self) -> str:
        return url_hash(self.url)


@dataclass
class Article:
    """A published thread created by the robot (news digest or hot-post digest)."""

    key: str
    board: str
    source_id: str
    source_name: str
    source_url: str
    title: str  # Chinese, catchy
    summary: str  # one or two sentences, used for meta description and lists
    body: list[str]  # Chinese paragraphs
    tags: list[str] = field(default_factory=list)
    original_title: str = ""
    lang: str = "en"
    published: str = ""
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    rewriter: str = ""

    @property
    def date_path(self) -> str:
        return self.created[:10].replace("-", "/")

    @property
    def slug(self) -> str:
        """Stable path used in URLs and as the comment thread key."""
        return f"{self.date_path}/{self.key}"

    @property
    def thread_key(self) -> str:
        return f"t:{self.slug}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Article":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})
