"""Data records that flow through fetch -> rewrite -> store -> build."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def url_hash(url: str) -> str:
    return hashlib.sha1(url.strip().encode("utf-8")).hexdigest()[:10]


_WORDS_MAX = 60


def url_words(title: str) -> str:
    """ASCII words for a URL: English titles as is, Chinese titles in pinyin (no tones)."""
    text = (title or "").strip()
    if not text:
        return ""
    if re.search(r"[\u4e00-\u9fff]", text):
        try:
            from pypinyin import lazy_pinyin

            text = " ".join(lazy_pinyin(text, errors=lambda x: [x]))
        except ImportError:  # no pinyin library: key-only URL
            return ""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().replace("'", "")
    words = re.findall(r"[a-z0-9]+", text)
    out: list[str] = []
    for w in words:
        if sum(len(x) + 1 for x in out) + len(w) > _WORDS_MAX:
            break
        out.append(w)
    return "-".join(out)


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
    show_link: bool = True

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
    show_link: bool = True  # False when the URL is a reposting site rather than the original publisher

    @property
    def date_path(self) -> str:
        return self.created[:10].replace("-", "/")

    @property
    def slug(self) -> str:
        """Stable path used in URLs and as the comment thread key."""
        return f"{self.date_path}/{self.key}"

    @property
    def path(self) -> str:
        """Public URL path (after /t/): date + readable words + key, e.g. 2026/11/09/fed-holds-rates-36a249c778.
        `slug` stays the storage path and comment-thread key, so views and comments keep working."""
        words = url_words(self.original_title) or url_words(self.title)
        return f"{self.date_path}/{words}-{self.key}" if words else self.slug

    @property
    def thread_key(self) -> str:
        return f"t:{self.slug}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Article":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})
