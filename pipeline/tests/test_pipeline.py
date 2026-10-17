import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from timelyreach import build, rewrite, update
from timelyreach.config import load_site, load_sources
from timelyreach.fetchers import parse_feed, parse_links
from timelyreach.models import Article, RawItem
from timelyreach.store import Store

FIX = Path(__file__).parent / "fixtures"


def _source(**kw):
    from timelyreach.config import Source

    base = dict(id="src", name="测试源", lang="en", board="news", fetch=[], limit=5)
    base.update(kw)
    return Source(**base)


def _item(n=1, **kw):
    base = dict(source_id="src", source_name="测试源", lang="en", board="news",
                url=f"https://example.com/a{n}", title=f"Headline {n}", summary="Something happened.")
    base.update(kw)
    return RawItem(**base)


class FakeProvider(rewrite.Provider):
    name = "fake"

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def rewrite(self, item):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return rewrite.Rewrite(title="中文标题" + item.title, summary="导读", body=["第一段", "第二段"], tags=["美国", "新闻"])


def test_parse_feed_extracts_items_and_cleans_html():
    items = parse_feed((FIX / "feed.xml").read_bytes(), _source())
    assert [i.title for i in items] == ["Fed Holds Rates Steady & Signals Cuts", "NYC Subway Fares Rise"]
    assert items[0].summary.startswith("The Federal Reserve kept rates unchanged")
    assert "<p>" not in items[0].summary
    assert items[0].published == "2026-10-17T12:00:00+00:00"


def test_parse_links_dedupes_and_skips_pagination():
    html = (FIX / "forum.html").read_text(encoding="utf-8")
    items = parse_links(html, "https://huaren.us/showforum.html?forumid=398", r"showtopic\.html\?topicid=\d+", _source(lang="zh"))
    assert [i.title for i in items] == ["湾区房价又涨了，大家还买吗？", "孩子申请藤校的经验分享一下"]
    assert items[0].url == "https://huaren.us/showtopic.html?topicid=3001&fid=398"


def test_config_files_are_consistent():
    site = load_site()
    boards = site.board_map
    for s in load_sources():
        assert s.board in boards and boards[s.board].kind == "auto", s.id
        assert s.lang in ("en", "zh")
        for spec in s.fetch:
            assert spec["type"] in ("rss", "rsshub", "links")
            if "pattern" in spec:
                re.compile(spec["pattern"])
    # user boards must match the CHECK constraint in supabase/schema.sql
    schema = (Path(__file__).resolve().parents[2] / "supabase/schema.sql").read_text(encoding="utf-8")
    user_boards = sorted(b.id for b in site.boards if b.kind == "user")
    allowed = sorted(re.findall(r"'(\w+)'", re.search(r"check \(board in \(([^)]*)\)\)", schema).group(1)))
    assert user_boards == allowed


def test_to_article_falls_back_to_next_provider():
    bad, good = FakeProvider(fail=True), FakeProvider()
    art = rewrite.to_article(_item(), [bad, good])
    assert art.title == "中文标题Headline 1" and art.rewriter == "fake"
    assert bad.calls == 1 and good.calls == 1


def test_free_translate_provider(monkeypatch):
    p = rewrite.FreeTranslateProvider()
    monkeypatch.setattr(p, "_translate", lambda text: "译:" + text)
    rw = p.rewrite(_item(summary="One. Two. Three. Four. Five."))
    assert rw.title == "译:Headline 1"
    assert rw.body[0] == "译:One. Two. Three. Four."
    zh = p.rewrite(_item(lang="zh", title="中文帖子标题", summary="内容"))
    assert zh.title == "中文帖子标题"


def test_claude_provider_uses_structured_output_and_handles_refusal():
    parsed = rewrite.Rewrite(title="标题", summary="导读", body=["段落"], tags=["标签"])
    calls = []

    def fake_parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=parsed)

    prov = rewrite.ClaudeProvider.__new__(rewrite.ClaudeProvider)
    prov.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=fake_parse)))
    assert prov.rewrite(_item()) is parsed
    kw = calls[0]
    assert kw["model"] == rewrite.CLAUDE_MODEL and kw["output_format"] is rewrite.Rewrite
    assert kw["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in kw["betas"]
    assert "Headline 1" in kw["messages"][0]["content"]

    prov.client.beta.messages.parse = lambda **kw: SimpleNamespace(stop_reason="refusal", parsed_output=None)
    with pytest.raises(RuntimeError):
        prov.rewrite(_item())


def test_run_update_stores_and_dedupes(tmp_path, monkeypatch):
    src = _source(limit=2)
    monkeypatch.setattr(update, "load_sources", lambda: [src])
    monkeypatch.setattr(update, "fetch_source", lambda s: [_item(1), _item(2), _item(3)])
    monkeypatch.setattr(update, "available_providers", lambda: [FakeProvider()])
    store = Store(tmp_path)
    assert update.run_update(store=store, max_new=10) == 2  # source limit
    store2 = Store(tmp_path)
    assert update.run_update(store=store2, max_new=10) == 1  # only the unseen one
    assert len(Store(tmp_path).load_articles()) == 3


def test_inactive_sources_are_skipped(tmp_path, monkeypatch):
    monkeypatch.delenv("TWITTER_AUTH_TOKEN", raising=False)
    src = _source(requires_env="TWITTER_AUTH_TOKEN")
    assert not src.is_active()
    monkeypatch.setenv("TWITTER_AUTH_TOKEN", "x")
    assert src.is_active()
    assert not _source(enabled=False).is_active()


def _article(n, title="美股大涨", board="finance"):
    return Article(key=f"{n:010x}", board=board, source_id="src", source_name="彭博社",
                   source_url=f"https://example.com/{n}", title=title, summary="导读 <b>摘要</b>",
                   body=["第一段", "第二段 <script>alert(1)</script>"], tags=["美股"],
                   created="2026-10-17T10:00:00+00:00")


def test_build_site(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    store = Store(tmp_path / "data")
    evil = _article(1, title='标题</script><script>alert("x")</script>')
    store.save_article(evil)
    for n in range(2, 50):
        store.save_article(_article(n))
    threads = [{"id": 7, "board": "chat", "title": "大家好", "body": "第一行\n第二行", "created_at": "2026-10-17T11:00:00+00:00",
                "author": {"username": "ivy", "display_name": "Ivy"}}]
    out = build.build_site(out=tmp_path / "site", store=store, user_threads=threads)

    page = (out / f"t/{evil.slug}.html").read_text(encoding="utf-8")
    assert "<script>alert" not in page
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        data = json.loads(block)
        assert data["@type"] in ("NewsArticle", "BreadcrumbList")
    assert '<link rel="canonical" href="https://timelyreach.com/t/2026/10/17/0000000001.html">' in page

    assert (out / "index.html").exists() and (out / "f/finance/index.html").exists()
    assert (out / "f/finance/2.html").exists()  # 48 finance articles, 40 per page
    assert (out / "p/7.html").read_text(encoding="utf-8").count("<p>第") == 2
    sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
    assert "https://timelyreach.com/p/7.html" in sitemap and "/t/2026/10/17/0000000002.html" in sitemap
    assert (out / "CNAME").read_text().strip() == "timelyreach.com"
    assert "Sitemap: https://timelyreach.com/sitemap.xml" in (out / "robots.txt").read_text()
    cfg = (out / "static/js/config.js").read_text(encoding="utf-8")
    assert '"supabaseUrl": ""' in cfg
    idx = json.loads((out / "search.json").read_text(encoding="utf-8"))
    assert len(idx) == 49 and idx[0]["u"].startswith("/t/")
    for name in ("thread", "login", "new", "user", "search", "about", "404"):
        assert (out / f"{name}.html").exists()
