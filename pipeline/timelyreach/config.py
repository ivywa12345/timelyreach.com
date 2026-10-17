"""Loading of site/source configuration and repository paths."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PIPELINE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = PIPELINE_DIR.parent
CONFIG_DIR = PIPELINE_DIR / "config"
DATA_DIR = Path(os.environ.get("TR_DATA_DIR", REPO_DIR / "data"))
SITE_SRC_DIR = REPO_DIR / "site"
OUTPUT_DIR = Path(os.environ.get("TR_OUTPUT_DIR", REPO_DIR / "_site"))


@dataclass
class Board:
    id: str
    name: str
    kind: str  # "auto" | "user"
    description: str = ""


@dataclass
class SiteConfig:
    name: str
    short_name: str
    tagline: str
    description: str
    keywords: str
    base_url: str
    language: str
    admin_github: str
    per_page: int
    boards: list[Board] = field(default_factory=list)

    @property
    def board_map(self) -> dict[str, Board]:
        return {b.id: b for b in self.boards}


@dataclass
class Source:
    id: str
    name: str
    lang: str
    board: str
    fetch: list[dict]
    limit: int = 5
    full_text: bool = False
    merge: bool = False
    enabled: bool = True
    requires_env: str | None = None

    def is_active(self) -> bool:
        if not self.enabled:
            return False
        if self.requires_env and not os.environ.get(self.requires_env):
            return False
        return True


def load_site(path: Path | None = None) -> SiteConfig:
    raw = yaml.safe_load((path or CONFIG_DIR / "site.yaml").read_text(encoding="utf-8"))
    boards = [Board(**b) for b in raw.pop("boards", [])]
    cfg = SiteConfig(boards=boards, **raw)
    cfg.base_url = os.environ.get("TR_BASE_URL", cfg.base_url).rstrip("/")
    return cfg


def load_sources(path: Path | None = None) -> list[Source]:
    raw = yaml.safe_load((path or CONFIG_DIR / "sources.yaml").read_text(encoding="utf-8"))
    return [Source(**s) for s in raw.get("sources", [])]
