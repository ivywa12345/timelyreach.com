from timelyreach.build import rich_text


def test_plain_paragraph_unchanged():
    assert str(rich_text("普通一段")) == "<p>普通一段</p>"


def test_markup_blocks():
    assert str(rich_text("## 标题")) == "<h2>标题</h2>"
    assert "<blockquote>" in str(rich_text("> 引用"))
    assert str(rich_text("- a\n- b")).count("<li>") == 2
    assert '<img src="/static/uploads/x.jpg"' in str(rich_text("![说明](/static/uploads/x.jpg)"))
    assert "<strong>粗</strong>" in str(rich_text("a **粗** b"))


def test_html_is_escaped_and_bad_links_ignored():
    out = str(rich_text("<script>alert(1)</script> [x](javascript:alert(1))"))
    assert "<script>" not in out and "<a " not in out


def test_tidy_blocks_normalises_model_output():
    from timelyreach.rewrite import tidy_blocks

    out = tidy_blocks(["导语", "# 大标题\n正文接在下面", "* 甲\n* 乙", "---", "## 孤零零的标题"])
    assert out == ["导语", "## 大标题", "正文接在下面", "- 甲\n- 乙"]


def test_seo_friendly_article_path_keeps_thread_key():
    from timelyreach.models import Article

    a = Article(key="abc123", board="finance", source_id="x", source_name="x", source_url="", title="美联储降息", summary="",
                body=[], original_title="Fed's Powell Signals Rate Cut", created="2026-11-09T02:50:00+00:00")
    assert a.path == "2026/11/09/feds-powell-signals-rate-cut-abc123"
    assert a.slug == "2026/11/09/abc123" and a.thread_key == "t:2026/11/09/abc123"
    b = Article(key="k1", board="finance", source_id="x", source_name="x", source_url="", title="做空机构Culper：英伟达", summary="",
                body=[], created="2026-11-09T02:50:00+00:00")
    assert b.path == "2026/11/09/zuo-kong-ji-gou-culper-ying-wei-da-k1"
