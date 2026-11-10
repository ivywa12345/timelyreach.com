import collections
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


@pytest.fixture(autouse=True)
def _no_real_inbox(monkeypatch):
    """run_update must not pick up files sitting in the repository's real inbox/."""
    monkeypatch.setattr(update, "run_inbox", lambda store: 0)
    monkeypatch.setattr(update, "run_weekly", lambda store, providers: None)  # weekend-only; tests opt in explicitly


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
    assert '<link rel="canonical" href="https://www.timelyreach.com/t/2026/10/17/0000000001">' in page

    assert (out / "index.html").exists() and (out / "f/finance/index.html").exists()
    assert (out / "f/finance/2.html").exists()  # 48 finance articles, 40 per page
    assert (out / "p/7.html").read_text(encoding="utf-8").count("<p>第") == 2
    sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
    assert "https://www.timelyreach.com/p/7" in sitemap and "/t/2026/10/17/0000000002" in sitemap
    assert (out / "CNAME").read_text().strip() == "www.timelyreach.com"
    assert "Sitemap: https://www.timelyreach.com/sitemap.xml" in (out / "robots.txt").read_text()
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
    assert replies[0][0] == 3 and "https://www.timelyreach.com/t/" in replies[0][1]
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


class _Resp:
    def __init__(self, code, body=None, text=""):
        self.status_code, self._body, self.text, self.headers = code, body, text, {"retry-after": "1"}
    def json(self): return self._body
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


_GOOD = {"choices": [{"message": {"content": '{"title":"标题","summary":"导读","body":["段落"],"tags":["a"]}'}}]}


def _llm(monkeypatch, **env):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MIN_INTERVAL", "0")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(rewrite.time, "sleep", lambda s: None)
    monkeypatch.setattr(rewrite.requests, "get", lambda *a, **k: _Resp(200, {"data": [{"id": "models/gemini-x"}]}))
    return rewrite.OpenAICompatibleProvider()


def test_llm_retries_a_rate_limit_on_the_same_model(monkeypatch):
    prov = _llm(monkeypatch)
    seq = iter([_Resp(429), _Resp(429), _Resp(200, _GOOD)])
    used = []
    monkeypatch.setattr(rewrite.requests, "post", lambda *a, **k: used.append(k["json"]["model"]) or next(seq))
    assert prov.rewrite(_item()).title == "标题"
    assert used == ["gemini-flash-lite-latest"] * 3 and not prov.dead  # ordinary news starts on the roomy lite model


def test_llm_moves_on_when_a_model_is_overloaded_or_missing_and_remembers_it(monkeypatch):
    prov = _llm(monkeypatch)
    used = []

    def post(*a, **k):
        m = k["json"]["model"]
        used.append(m)
        if m == "gemini-flash-lite-latest":
            return _Resp(503, text="overloaded")
        if m == "gemini-flash-latest":
            return _Resp(404, text="not found")
        return _Resp(200, _GOOD)

    monkeypatch.setattr(rewrite.requests, "post", post)
    assert prov.rewrite(_item()).title == "标题"
    assert used == ["gemini-flash-lite-latest"] * 2 + ["gemini-flash-latest", "gemini-2.5-flash-lite"]
    used.clear()
    prov.rewrite(_item())
    assert used == ["gemini-2.5-flash-lite"]  # later items go straight to the model that works


def test_llm_brief_and_picks_try_the_better_model_first(monkeypatch):
    prov = _llm(monkeypatch)
    used = []
    monkeypatch.setattr(rewrite.requests, "post", lambda *a, **k: used.append(k["json"]["model"]) or _Resp(200, _GOOD))
    prov.rewrite(_item(lang="brief"))
    prov.rewrite(_item(lang="editor"))
    prov.rewrite(_item())
    assert used == ["gemini-flash-latest", "gemini-flash-latest", "gemini-flash-lite-latest"]


def test_llm_gives_up_for_the_run_when_every_model_is_exhausted(monkeypatch):
    prov = _llm(monkeypatch, LLM_MODEL="only-model")
    calls = []
    monkeypatch.setattr(rewrite.requests, "post", lambda *a, **k: calls.append(1) or _Resp(429, text="quota"))
    with pytest.raises(RuntimeError, match="no usable llm model"):
        prov.rewrite(_item())
    assert prov.exhausted and len(calls) == 3  # first try + 2 retries
    with pytest.raises(RuntimeError, match="exhausted"):
        prov.rewrite(_item())
    assert len(calls) == 3  # later items fail fast


def test_llm_bad_key_is_not_retried_on_other_models(monkeypatch):
    prov = _llm(monkeypatch)
    calls = []
    monkeypatch.setattr(rewrite.requests, "post", lambda *a, **k: calls.append(1) or _Resp(403, text="denied"))
    with pytest.raises(requests.HTTPError):
        prov.rewrite(_item())
    assert len(calls) == 1


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
    assert providers[0].models == list(rewrite.OpenAICompatibleProvider.DEFAULT_MODELS)
    monkeypatch.setenv("LLM_API_KEY", "sk-not-gemini")
    monkeypatch.delenv("LLM_BASE_URL")
    assert rewrite.available_providers() == []


def test_gemini_key_alone_is_enough(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("TR_DISABLE_FREE_TRANSLATE", "1")
    for key in ("AIzaSyExample", "AQ.Ab8RN6examplekey"):  # old and new Google AI Studio key formats
        monkeypatch.setenv("LLM_API_KEY", key)
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        providers = rewrite.available_providers()
        assert [p.name for p in providers] == ["openai-compatible"]
        assert providers[0].base_url == rewrite.GEMINI_BASE_URL
    monkeypatch.setenv("LLM_API_KEY", "sk-other")
    monkeypatch.delenv("LLM_BASE_URL")
    assert rewrite.available_providers() == []  # not a Gemini key: needs an explicit base URL




def test_inbox_publishes_valid_files_and_rejects_bad_ones(tmp_path):
    from timelyreach.inbox import run_inbox

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    good = {"title": "美联储降息", "summary": "导语", "body": ["第一段", "第二段"], "source_name": "华尔街日报",
            "source_url": "https://wsj.com/a", "board": "finance", "tags": ["美股"], "created": "2026-10-27T13:05:00Z"}
    (inbox / "a.json").write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
    own = {"title": "我的看法", "body": ["只有正文"], "board": "nope", "author": "站长"}
    (inbox / "b.json").write_text(json.dumps(own, ensure_ascii=False), encoding="utf-8")
    (inbox / "c.json").write_text('{"title": "没正文", "body": []}', encoding="utf-8")
    store = Store(tmp_path / "data")
    assert run_inbox(store, inbox) == 2
    arts = {a.title: a for a in store.load_articles()}
    assert arts["美联储降息"].source_name == "华尔街日报" and arts["美联储降息"].created.startswith("2026-10-27T13:05")
    assert arts["我的看法"].board == "finance" and arts["我的看法"].source_id == "editor" and not arts["我的看法"].show_link
    assert not list(inbox.glob("*.json")) and (inbox / "rejected" / "c.json").exists()
    (inbox / "a.json").write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
    assert run_inbox(store, inbox) == 0  # same URL is not published twice


def test_same_story_from_two_feeds_is_posted_once(tmp_path, monkeypatch):
    a = _source(limit=5)
    b = _source(limit=5)
    b.id = "src2"
    monkeypatch.setattr(update, "load_sources", lambda: [a, b])
    feeds = {
        "src": [_item(1, title="Opinion | Anthropic’s IPO Will Be a Win")],
        "src2": [_item(2, title="Opinion | Anthropic's IPO Will Be a Win"), _item(3)],
    }
    monkeypatch.setattr(update, "fetch_source", lambda s: feeds[s.id])
    monkeypatch.setattr(update, "available_providers", lambda: [FakeProvider()])
    monkeypatch.setattr(update, "run_picks", lambda store, providers: 0)
    monkeypatch.setattr(update, "run_brief", lambda store, providers: None)
    monkeypatch.setattr(update, "credit_reposted_sources", lambda store: 0)
    assert update.run_update(store=Store(tmp_path), max_new=10) == 2


def test_board_lists_show_top_five_hot_of_four_days_then_newest_first(tmp_path):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    store = Store(tmp_path / "data")

    def art(key, hours_old):
        a = Article(key=key, board="finance", source_id="s", source_name="S", source_url=f"https://x/{key}",
                    title=f"title {key}", summary="s", body=["b"], rewriter="fake",
                    created=(now - timedelta(hours=hours_old)).isoformat(timespec="seconds"))
        store.save_article(a)
        return a

    fresh = [art(f"n{i}", i) for i in range(10)]            # n0 newest ... n9 oldest
    old_hot = art("old", 24 * 6)                              # six days old: never "hot"
    stats = {fresh[7].thread_key: (500, 0), fresh[3].thread_key: (100, 20), fresh[9].thread_key: (95, 0),
             fresh[5].thread_key: (60, 0), fresh[8].thread_key: (35, 0), fresh[1].thread_key: (31, 0),
             fresh[2].thread_key: (6, 0), old_hot.thread_key: (9999, 0)}
    out = build.build_site(tmp_path / "out", store=store, user_threads=[], stats=stats)
    page = (out / "f/finance/index.html").read_text(encoding="utf-8")
    order = [m for m in re.findall(r"title (n\d|old)", page)]
    seen = []
    [seen.append(x) for x in order if x not in seen]
    # top five by views + 5*replies, then newest first; n1 is sixth, n2 has too few views to count as hot
    assert seen[:5] == ["n7", "n3", "n9", "n5", "n8"]
    assert seen[5:] == ["n0", "n1", "n2", "n4", "n6", "old"]


def test_board_lists_are_plain_newest_first_without_stats(tmp_path):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    store = Store(tmp_path / "data")
    for i in range(3):
        store.save_article(Article(key=f"k{i}", board="finance", source_id="s", source_name="S", source_url=f"https://x/{i}",
                                   title=f"title k{i}", summary="s", body=["b"], rewriter="fake",
                                   created=(now - timedelta(hours=i)).isoformat(timespec="seconds")))
    out = build.build_site(tmp_path / "out", store=store, user_threads=[], stats={})
    page = (out / "f/finance/index.html").read_text(encoding="utf-8")
    assert page.index("title k0") < page.index("title k1") < page.index("title k2")


def test_analytics_beacon_only_with_token(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    monkeypatch.delenv("CF_ANALYTICS_TOKEN", raising=False)
    monkeypatch.setattr(build, "load_site", lambda: __import__("dataclasses").replace(load_site(), analytics_token=""))
    out = build.build_site(tmp_path / "a", store=store, user_threads=[])
    assert "cloudflareinsights" not in (out / "index.html").read_text(encoding="utf-8")
    monkeypatch.setenv("CF_ANALYTICS_TOKEN", 'abc123"><script>x')
    out = build.build_site(tmp_path / "b", store=store, user_threads=[])
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'data-cf-beacon=\'{"token": "abc123scriptx"}\'' in html  # sanitized to letters and digits


def test_daily_brief_board_stays_strictly_newest_first(tmp_path):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    store = Store(tmp_path / "data")
    arts = []
    for i in range(3):
        a = Article(key=f"b{i}", board="brief", source_id="brief", source_name="S", source_url=f"https://x/b{i}",
                    title=f"brief day {i}", summary="s", body=["b"], rewriter="fake",
                    created=(now - timedelta(days=i)).isoformat(timespec="seconds"))
        store.save_article(a)
        arts.append(a)
    stats = {arts[2].thread_key: (500, 3)}  # the oldest brief is the most read
    out = build.build_site(tmp_path / "out", store=store, user_threads=[], stats=stats)
    page = (out / "f/brief/index.html").read_text(encoding="utf-8")
    assert page.index("brief day 0") < page.index("brief day 1") < page.index("brief day 2")


def test_ads_are_off_without_a_publisher_id_and_wired_with_one(tmp_path, monkeypatch):
    from dataclasses import replace

    store = Store(tmp_path / "data")
    store.save_article(Article(key="a0", board="finance", source_id="s", source_name="S", source_url="https://x/0", title="ad test",
                               summary="s", body=["b"], rewriter="fake"))
    base = load_site()
    monkeypatch.delenv("ADSENSE_CLIENT", raising=False)
    monkeypatch.setattr(build, "load_site", lambda: replace(base, adsense_client="", ad_slots={}))
    out = build.build_site(tmp_path / "off", store=store, user_threads=[], stats={})
    assert "adsbygoogle" not in "".join(p.read_text(encoding="utf-8") for p in (out).rglob("*.html"))
    assert not (out / "ads.txt").exists()
    assert "隐私政策" in (out / "privacy.html").read_text(encoding="utf-8")

    on = replace(base, adsense_client="ca-pub-1234567890123456",
                 ad_slots={"article_top": "1111111111", "sidebar": "22222222\"><script>", "list": "3333333333"})
    monkeypatch.setattr(build, "load_site", lambda: on)
    out = build.build_site(tmp_path / "on", store=store, user_threads=[], stats={})
    assert (out / "ads.txt").read_text() == "google.com, pub-1234567890123456, DIRECT, f08c47fec0942fa0\n"
    article = next((out / "t").rglob("*.html")).read_text(encoding="utf-8")
    assert "adsbygoogle.js?client=ca-pub-1234567890123456" in article
    assert 'data-ad-slot="1111111111"' in article          # configured slot rendered
    assert "22222222" not in article                        # malformed slot id is dropped, never injected
    assert 'data-ad-slot="3333333333"' not in article       # the list slot only appears on board pages
    board = (out / "f/finance/index.html").read_text(encoding="utf-8")
    assert "adsbygoogle.js" in board
    login = (out / "login.html").read_text(encoding="utf-8")
    assert "adsbygoogle" not in login                       # no ads on app pages


def _week_store(tmp_path, now, n=8, with_briefs=True):
    from datetime import timedelta

    store = Store(tmp_path / "data")
    for i in range(n):
        store.save_article(Article(key=f"w{i}", board="finance" if i % 2 else "news", source_id="s", source_name="S",
                                   source_url=f"https://x/w{i}", title=f"story {i}", summary=f"story {i} deck", body=["b"],
                                   rewriter="fake", created=(now - timedelta(days=i % 6, hours=i)).isoformat(timespec="seconds")))
    if with_briefs:
        store.save_article(Article(key="bf1", board="brief", source_id="brief", source_name="B", source_url="https://x/b",
                                   title="11月6日财经早报：一件事", summary="s", body=["b"], rewriter="fake",
                                   created=(now - timedelta(days=1)).isoformat(timespec="seconds")))
    return store


def test_weekly_review_is_published_once_on_saturday_only(tmp_path):
    from datetime import datetime, timezone
    from timelyreach.weekly import run_weekly

    sat = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)   # Saturday 10:00 ET
    store = _week_store(tmp_path, sat)
    stats = {"t:" + a.slug: (v, 0) for a, v in ((x, 100 - i) for i, x in enumerate(store.load_articles()))}
    art = run_weekly(store, [rewrite.FreeTranslateProvider()], now=sat, stats=stats)
    assert art and art.board == "brief" and art.title.startswith("本周回顾（11月2日–11月6日）")
    assert "story" in "\n".join(art.body) and "本周每日早报标题" in "\n".join(art.body)
    assert run_weekly(store, [rewrite.FreeTranslateProvider()], now=sat, stats=stats) is None            # once per week
    sun = datetime(2026, 11, 8, 15, 0, tzinfo=timezone.utc)
    assert run_weekly(store, [rewrite.FreeTranslateProvider()], now=sun, stats=stats) is None            # same week


def test_weekly_review_skips_weekdays_early_morning_and_thin_weeks(tmp_path):
    from datetime import datetime, timezone
    from timelyreach.weekly import run_weekly

    fri = datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)
    store = _week_store(tmp_path, fri)
    assert run_weekly(store, [rewrite.FreeTranslateProvider()], now=fri, stats={}) is None                # weekday
    early = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)                                             # Saturday 5:00 ET
    assert run_weekly(store, [rewrite.FreeTranslateProvider()], now=early, stats={}) is None
    sat = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)
    thin = _week_store(tmp_path / "thin", sat, n=3, with_briefs=False)
    assert run_weekly(thin, [rewrite.FreeTranslateProvider()], now=sat, stats={}) is None                 # too few articles


def test_google_analytics_is_off_without_an_id_and_skips_owner_pages(tmp_path, monkeypatch):
    from dataclasses import replace

    store = Store(tmp_path / "data")
    base = load_site()
    monkeypatch.delenv("GA_MEASUREMENT_ID", raising=False)
    monkeypatch.setattr(build, "load_site", lambda: replace(base, ga_measurement_id="", gtm_id=""))
    out = build.build_site(tmp_path / "off", store=store, user_threads=[], stats={})
    assert "googletagmanager" not in (out / "index.html").read_text(encoding="utf-8")

    monkeypatch.setattr(build, "load_site", lambda: replace(base, ga_measurement_id="G-ABC123DEF4", gtm_id=""))
    out = build.build_site(tmp_path / "on", store=store, user_threads=[], stats={})
    assert "gtag/js?id=G-ABC123DEF4" in (out / "index.html").read_text(encoding="utf-8")
    assert "googletagmanager" not in (out / "admin.html").read_text(encoding="utf-8")
    assert "googletagmanager" not in (out / "analytics.html").read_text(encoding="utf-8")

    monkeypatch.setattr(build, "load_site", lambda: replace(base, ga_measurement_id='G-1"><script>x', gtm_id=""))
    out = build.build_site(tmp_path / "bad", store=store, user_threads=[], stats={})
    assert "googletagmanager" not in (out / "index.html").read_text(encoding="utf-8")    # malformed id is dropped


def test_google_tag_manager_container_is_loaded_with_consent_defaults_first(tmp_path, monkeypatch):
    from dataclasses import replace

    store = Store(tmp_path / "data")
    base = load_site()
    monkeypatch.delenv("GTM_ID", raising=False)
    monkeypatch.setattr(build, "load_site", lambda: replace(base, gtm_id="GTM-K256QZK2", ga_measurement_id=""))
    out = build.build_site(tmp_path / "gtm", store=store, user_threads=[], stats={})
    page = (out / "index.html").read_text(encoding="utf-8")
    assert "gtm.js?id=" in page and '"GTM-K256QZK2"' in page and "gtag/js?id=" not in page
    assert page.index('gtag("consent", "default"') < page.index("gtm.js?id=")      # consent defaults before the container loads
    assert "gtm.js" not in (out / "admin.html").read_text(encoding="utf-8")
    monkeypatch.setattr(build, "load_site", lambda: replace(base, gtm_id='GTM-1"><script>', ga_measurement_id=""))
    out = build.build_site(tmp_path / "bad", store=store, user_threads=[], stats={})
    assert "gtm.js" not in (out / "index.html").read_text(encoding="utf-8")


def test_home_page_continues_with_latest_posts_not_already_on_the_front(tmp_path):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    store = Store(tmp_path / "data")
    for i in range(20):
        store.save_article(Article(key=f"h{i:02d}", board="finance" if i % 2 else "news", source_id="s", source_name="S",
                                   source_url=f"https://x/{i}", title=f"home story {i:02d}", summary="s", body=["b"], rewriter="fake",
                                   created=(now - timedelta(minutes=i)).isoformat(timespec="seconds")))
    out = build.build_site(tmp_path / "out", store=store, user_threads=[], stats={})
    page = (out / "index.html").read_text(encoding="utf-8")
    block = page[page.index("front-latest"):]
    block = block[:block.index("</ul>")]
    titles = re.findall(r"home story (\d\d)", block)
    assert len(titles) == 6 and len(set(titles)) == 6
    front = page[:page.index("front-latest")]
    assert not set(titles) & set(re.findall(r"home story (\d\d)", front.split('class="subs"')[0] + front.split('class="subs"')[1]))  # nothing repeated


def test_front_page_headlines_have_no_duplicates(tmp_path):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    arts = []
    for i in range(6):
        arts.append(Article(key=f"d{i}", board="finance" if i % 2 else "news", source_id="s", source_name="S", source_url=f"https://x/{i}",
                            title=f"t{i}", summary="s", body=["b"], rewriter="inbox" if i < 3 else "fake",
                            created=(now - timedelta(hours=i)).isoformat(timespec="seconds")))
    arts.append(Article(key="e1", board="finance", source_id="editor", source_name="站长", source_url="", title="owner", summary="s",
                        body=["b"], rewriter="inbox", created=(now - timedelta(hours=1)).isoformat(timespec="seconds")))
    keys = [a.key for a in build._front_page(sorted(arts, key=lambda a: a.created, reverse=True), n=10)]
    assert len(keys) == len(set(keys)) == 7


def test_public_urls_have_no_html_extension_so_pages_never_redirect(tmp_path):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    store = Store(tmp_path / "data")
    for i in range(45):  # more than one board page (40 per page)
        store.save_article(Article(key=f"p{i:02d}", board="finance", source_id="s", source_name="S", source_url=f"https://x/{i}",
                                   title=f"page story {i:02d}", summary="s", body=["b"], rewriter="fake",
                                   created=(now - timedelta(minutes=i)).isoformat(timespec="seconds")))
    out = build.build_site(tmp_path / "out", store=store, user_threads=[], stats={})
    base = load_site().base_url
    page2 = (out / "f/finance/2.html").read_text(encoding="utf-8")          # file name stays, public URL does not
    assert f'<link rel="canonical" href="{base}/f/finance/2">' in page2
    assert f'href="{base}/about"' not in page2 and 'rel="prev" href="/f/finance/"' in page2
    assert f'<link rel="canonical" href="{base}/about">' in (out / "about.html").read_text(encoding="utf-8")
    assert f"{base}/about</loc>" in (out / "sitemap.xml").read_text(encoding="utf-8")
    for name in ("index.html", "f/finance/index.html", "about.html"):
        html = (out / name).read_text(encoding="utf-8")
        assert not re.findall(r'href="/(?:login|new|user|thread|about|privacy|search)\.html', html), name
        assert 'href="/f/finance/2.html"' not in html, name
    assert "Disallow: /login\n" in (out / "robots.txt").read_text(encoding="utf-8")


def test_every_source_gets_a_turn_when_the_run_budget_is_small(tmp_path, monkeypatch):
    from timelyreach.config import Source

    def src(sid):
        return Source(id=sid, name=sid, lang="en", board="news", fetch=[{"type": "rss", "url": "https://x"}], limit=5)

    sources = [src("first"), src("second"), src("last")]
    feeds = {s.id: [_item(i, source_id=s.id, url=f"https://{s.id}.example/{i}", title=f"{s.id} headline {i}") for i in range(5)] for s in sources}
    monkeypatch.setattr(update, "load_sources", lambda: sources)
    monkeypatch.setattr(update, "fetch_source", lambda s: feeds[s.id])
    monkeypatch.setattr(update, "available_providers", lambda: [FakeProvider()])
    monkeypatch.setattr(update, "run_picks", lambda store, providers: 0)
    monkeypatch.setattr(update, "run_brief", lambda store, providers: None)
    monkeypatch.setattr(update, "credit_reposted_sources", lambda store: 0)
    store = Store(tmp_path)
    assert update.run_update(store=store, max_new=6) == 6
    by_source = collections.Counter(a.source_id for a in Store(tmp_path).load_articles())
    assert by_source == {"first": 2, "second": 2, "last": 2}   # the last source is not starved
