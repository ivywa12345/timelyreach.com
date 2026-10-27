import json
import requests
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
    assert [i.title for i in items] == ["湾区房价又涨了，大家还买吗？", "孩子申请藤校的经验分享一下", "485 二面经验"]
    assert items[0].url == "https://huaren.us/showtopic.html?topicid=3001&fid=398"


def test_config_files_are_consistent():
    site = load_site()
    boards = site.board_map
    for s in load_sources():
        assert s.board in boards and boards[s.board].kind == "auto", s.id
        assert s.lang in ("en", "zh", "zh-news")
        for spec in s.fetch:
            assert spec["type"] in ("rss", "rsshub", "links", "wxc_finance")
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
    monkeypatch.setattr(update, "run_picks", lambda store, providers: 0)
    monkeypatch.setattr(update, "run_brief", lambda store, providers: None)
    monkeypatch.setattr(update, "credit_reposted_sources", lambda store: 0)
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


def test_free_translate_retries_then_falls_back_to_mymemory(monkeypatch):
    import deep_translator

    calls = []

    class Google:
        def __init__(self, **kw): pass
        def translate(self, text):
            calls.append("g")
            raise RuntimeError("too many requests")

    class MyMemory:
        def __init__(self, **kw): pass
        def translate(self, text):
            calls.append("m")
            return "译文"

    monkeypatch.setattr(deep_translator, "GoogleTranslator", Google)
    monkeypatch.setattr(deep_translator, "MyMemoryTranslator", MyMemory)
    monkeypatch.setattr(rewrite.time, "sleep", lambda s: None)
    assert rewrite.FreeTranslateProvider()._translate("hello") == "译文"
    assert calls == ["g", "g", "g", "m"]


ISSUE_BODY = """### 原文链接

https://www.wsj.com/finance/some-story-123

### 我的点评

VIX 期限结构倒挂，短期要小心。
0DTE 卖方这周别太激进。

### 原文摘录（可选，最多两句）

_No response_

### 发到哪个版块

finance 财经股市
"""


def test_issue_form_parsing_and_owner_check(monkeypatch):
    from timelyreach import picks

    fields = picks.parse_issue_form(ISSUE_BODY)
    assert fields["原文链接"] == "https://www.wsj.com/finance/some-story-123"
    assert fields["原文摘录（可选，最多两句）"] == ""
    assert picks.site_name("https://www.wsj.com/x") == "华尔街日报"
    issue = {"number": 3, "title": "[荐读] 波动率要起飞了", "body": ISSUE_BODY, "user": {"login": "ivywa12345"}, "labels": []}
    assert picks.is_pick(issue, "ivywa12345")
    assert not picks.is_pick({**issue, "user": {"login": "stranger"}}, "ivywa12345")

    monkeypatch.setattr(picks, "page_meta", lambda url: ("Volatility Is Back", "Traders brace."))
    item = picks.issue_to_item(issue)
    assert item.title == "波动率要起飞了" and item.board == "finance" and item.lang == "editor"
    assert item.text.startswith("VIX 期限结构倒挂") and "Volatility Is Back" in item.summary


def test_run_picks_posts_once_and_closes_issue(tmp_path, monkeypatch):
    from timelyreach import picks

    replies = []

    class FakeGH:
        def __init__(self, repo, token): pass
        def open_issues(self):
            return [
                {"number": 3, "title": "[荐读] 波动率要起飞了", "body": ISSUE_BODY, "user": {"login": "ivywa12345"}, "labels": []},
                {"number": 4, "title": "[荐读] spam", "body": ISSUE_BODY, "user": {"login": "stranger"}, "labels": []},
            ]
        def reply_and_close(self, number, text):
            replies.append((number, text))

    monkeypatch.setenv("GITHUB_REPOSITORY", "ivywa12345/timelyreach.com")
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setattr(picks, "GitHubIssues", FakeGH)
    monkeypatch.setattr(picks, "page_meta", lambda url: ("", ""))
    store = Store(tmp_path)
    assert picks.run_picks(store, [rewrite.FreeTranslateProvider()]) == 1
    art = store.load_articles()[0]
    assert art.source_id == "editor" and art.board == "finance" and art.body[0].startswith("VIX")
    assert replies[0][0] == 3 and "https://timelyreach.com/t/" in replies[0][1]
    assert picks.run_picks(store, [rewrite.FreeTranslateProvider()]) == 0  # not reposted


def test_video_and_podcast_links_are_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "load_sources", lambda: [_source()])
    monkeypatch.setattr(update, "fetch_source", lambda s: [
        _item(1, url="https://www.bloomberg.com/news/videos/2026-10-17/quiz-video"),
        _item(2, url="https://www.bloomberg.com/news/articles/2026-10-17/real-story"),
    ])
    monkeypatch.setattr(update, "available_providers", lambda: [FakeProvider()])
    monkeypatch.setattr(update, "run_picks", lambda store, providers: 0)
    monkeypatch.setattr(update, "run_brief", lambda store, providers: None)
    monkeypatch.setattr(update, "credit_reposted_sources", lambda store: 0)
    assert update.run_update(store=Store(tmp_path), max_new=10) == 1


def test_daily_brief_once_per_trading_day(tmp_path):
    from datetime import datetime, timezone

    from timelyreach import brief

    store = Store(tmp_path)
    for n in range(5):
        a = _article(n + 100, title=f"财经新闻{n}")
        a.created = "2026-10-26T08:00:00+00:00"
        store.save_article(a)
    monday_7am_et = datetime(2026, 10, 26, 11, 0, tzinfo=timezone.utc)
    art = brief.run_brief(store, [rewrite.FreeTranslateProvider()], now=monday_7am_et)
    assert art.board == "brief" and art.title.startswith("10月26日财经早报")
    assert any("财经新闻0" in p for p in art.body)
    assert brief.run_brief(store, [rewrite.FreeTranslateProvider()], now=monday_7am_et) is None  # once a day
    saturday = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
    assert brief.run_brief(Store(tmp_path / "x"), [rewrite.FreeTranslateProvider()], now=saturday) is None
    too_early = datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc)  # 4am ET
    assert brief.run_brief(store, [rewrite.FreeTranslateProvider()], now=too_early) is None


def test_wenxuecity_finance_top_level_posts_credit_the_outlet():
    from timelyreach.fetchers import parse_wxc_finance

    html = (FIX / "wxc_finance.html").read_text(encoding="utf-8")
    items = parse_wxc_finance(html, "https://bbs.wenxuecity.com/financenews/", _source(lang="zh-news", board="finance"))
    assert [(i.title, i.source_name) for i in items] == [
        ("从这30个细节，看Costco的能力自信", "砺石商业评论"),
        ("长鑫科技合并利润看起来很美,但归母公司利润很骨感", "财联社"),
    ]  # the indented reply by justbuy168 is skipped
    assert items[0].url == "https://bbs.wenxuecity.com/financenews/34826.html"
    assert all(not i.show_link for i in items)


def test_wxc_source_reads_both_page_layouts():
    from timelyreach.fetchers import wxc_source

    news = '<div id="postmeta"> 文章来源: <span itemprop="author">华尔街见闻</span> 于 2026-10-22'
    bbs = '来源:\n <a href="//x" class="username" >\n <span>第一财经</span>\n </a>\n 于 <span class="date">'
    assert wxc_source(news) == "华尔街见闻"
    assert wxc_source(bbs) == "第一财经"
    assert wxc_source("<p>no credit here</p>") == ""


def test_zh_news_free_fallback_keeps_short_credited_excerpt():
    item = _item(lang="zh-news", source_name="华尔街见闻", title="美股最不希望涨的板块涨了",
                 text="第一段内容，医疗板块走强。\n第二段内容，估值折价消失。\n短\n第三段内容。")
    rw = rewrite.FreeTranslateProvider().rewrite(item)
    assert rw.title == "美股最不希望涨的板块涨了"
    assert rw.body[-1].startswith("（本文据华尔街见闻报道整理")
    assert "短" not in rw.body and "文学城" not in "".join(rw.body)


def test_old_wenxuecity_posts_are_recredited(tmp_path, monkeypatch):
    store = Store(tmp_path)
    a = _article(7, title="旧文章")
    a.source_id, a.source_name, a.board = "wenxuecity", "文学城", "news"
    a.body = ["正文", "（以上为文学城内容摘译，详情请看原文。）"]
    a.tags = ["文学城"]
    store.save_article(a)
    monkeypatch.setattr(update, "fetch_article", lambda url: ("", "中央社"))
    assert update.credit_reposted_sources(store) == 1
    fixed = Store(tmp_path).load_articles()[0]
    assert fixed.source_name == "中央社" and not fixed.show_link
    assert "文学城" not in "".join(fixed.body + fixed.tags)
    assert update.credit_reposted_sources(Store(tmp_path)) == 0


def test_brief_line_does_not_repeat_the_title():
    from timelyreach.brief import _line

    a = _article(9, title="美联储按兵不动")
    a.summary = "美联储按兵不动，市场押注年内降息两次。"
    assert _line(a) == "美联储按兵不动：市场押注年内降息两次。"
    a.summary = "另一句导读"
    assert _line(a) == "美联储按兵不动：另一句导读"


def test_openai_compatible_retries_rate_limits_then_gives_up(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MIN_INTERVAL", "0")
    monkeypatch.setattr(rewrite.time, "sleep", lambda s: None)
    good = {"choices": [{"message": {"content": '{"title":"标题","summary":"导读","body":["段落"],"tags":["a"]}'}}]}

    class Resp:
        def __init__(self, code, body=None):
            self.status_code, self._body, self.headers = code, body, {"retry-after": "1"}
        def json(self): return self._body
        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(response=self)

    seq = iter([Resp(429), Resp(429), Resp(200, good)])
    monkeypatch.setattr(rewrite.requests, "post", lambda *a, **k: next(seq))
    prov = rewrite.OpenAICompatibleProvider()
    assert prov.rewrite(_item()).title == "标题" and not prov.exhausted

    calls = []
    monkeypatch.setattr(rewrite.requests, "post", lambda *a, **k: calls.append(1) or Resp(429))
    with pytest.raises(requests.HTTPError):
        prov.rewrite(_item())
    assert prov.exhausted and len(calls) == 4  # first try + 3 retries
    with pytest.raises(RuntimeError):  # later items fail fast, no more requests
        prov.rewrite(_item())
    assert len(calls) == 4


def test_env_values_are_trimmed_and_provider_is_detected(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("TR_DISABLE_FREE_TRANSLATE", "1")
    monkeypatch.setenv("LLM_API_KEY", "AIza-secret\n")
    monkeypatch.setenv("LLM_BASE_URL", " https://generativelanguage.googleapis.com/v1beta/openai/ \n")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    providers = rewrite.available_providers()
    assert [p.name for p in providers] == ["openai-compatible"]
    assert providers[0].api_key == "AIza-secret"
    assert providers[0].base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert providers[0].model == "gemini-2.5-flash"
    monkeypatch.delenv("LLM_BASE_URL")
    assert rewrite.available_providers() == []


def test_gemini_key_alone_is_enough(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("TR_DISABLE_FREE_TRANSLATE", "1")
    monkeypatch.setenv("LLM_API_KEY", "AIzaSyExample")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    providers = rewrite.available_providers()
    assert [p.name for p in providers] == ["openai-compatible"]
    assert providers[0].base_url == rewrite.GEMINI_BASE_URL
    monkeypatch.setenv("LLM_API_KEY", "sk-other")
    monkeypatch.delenv("LLM_BASE_URL")
    assert rewrite.available_providers() == []  # not a Gemini key: needs an explicit base URL
