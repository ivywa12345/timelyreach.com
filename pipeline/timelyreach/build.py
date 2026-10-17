"""Render the static site into _site/ (deployed to GitHub Pages)."""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from .config import OUTPUT_DIR, SITE_SRC_DIR, SiteConfig, load_site
from .models import Article
from .store import Store
from .supabase_read import load_user_threads, supabase_settings

log = logging.getLogger(__name__)


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(SITE_SRC_DIR / "templates"),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["date_cn"] = lambda iso: iso[:10] if iso else ""
    env.filters["datetime_cn"] = lambda iso: iso[:16].replace("T", " ") if iso else ""
    # JSON-LD inside <script>: must not be HTML-escaped, but must not be able to close the tag
    env.filters["tojson_safe"] = lambda obj: Markup(
        json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )
    return env


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _paginate(items: list, per_page: int) -> list[list]:
    pages = [items[i : i + per_page] for i in range(0, len(items), per_page)]
    return pages or [[]]


def board_page_url(board_id: str, page: int) -> str:
    return f"/f/{board_id}/" if page == 1 else f"/f/{board_id}/{page}.html"


def build_site(out: Path = OUTPUT_DIR, store: Store | None = None, user_threads: list[dict] | None = None) -> Path:
    site = load_site()
    store = store or Store()
    articles = store.load_articles()
    if user_threads is None:
        user_threads = load_user_threads()
    sb_url, sb_key = supabase_settings()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copytree(SITE_SRC_DIR / "static", out / "static")

    env = _env()
    common = {
        "site": site,
        "boards": site.boards,
        "board_map": site.board_map,
        "now": now,
        "year": now[:4],
        "supabase_on": bool(sb_url and sb_key),
    }

    def render(template: str, dest: str, **ctx) -> None:
        _write(out / dest, env.get_template(template).render(**common, **ctx))

    by_board: dict[str, list[Article]] = {b.id: [] for b in site.boards}
    for a in articles:
        by_board.setdefault(a.board, []).append(a)

    # Home page
    render(
        "index.html",
        "index.html",
        canonical=site.base_url + "/",
        headlines=articles[:8],
        by_board={k: v[:8] for k, v in by_board.items()},
        latest_users=user_threads[:10],
    )

    # Board list pages
    for board in site.boards:
        items = by_board.get(board.id, [])
        pages = _paginate(items, site.per_page)
        for n, chunk in enumerate(pages, start=1):
            url = board_page_url(board.id, n)
            render(
                "board.html",
                url.lstrip("/") + ("index.html" if url.endswith("/") else ""),
                canonical=site.base_url + url,
                board=board,
                items=chunk,
                page=n,
                pages=len(pages),
                prev_url=board_page_url(board.id, n - 1) if n > 1 else None,
                next_url=board_page_url(board.id, n + 1) if n < len(pages) else None,
                user_threads=[t for t in user_threads if t.get("board") == board.id][:50] if n == 1 else [],
            )

    # Article pages
    for i, a in enumerate(articles):
        related = [r for r in by_board.get(a.board, []) if r.key != a.key][:6]
        render(
            "article.html",
            f"t/{a.slug}.html",
            canonical=f"{site.base_url}/t/{a.slug}.html",
            a=a,
            board=site.board_map.get(a.board),
            related=related,
        )

    # Reader threads (static copies for search engines; comments load live)
    for t in user_threads:
        render(
            "user_thread.html",
            f"p/{t['id']}.html",
            canonical=f"{site.base_url}/p/{t['id']}.html",
            t=t,
            board=site.board_map.get(t.get("board")),
        )

    # App pages (dynamic, rendered in the browser)
    for name in ("thread", "login", "new", "user", "search", "about", "404"):
        render(f"{name}.html", f"{name}.html", canonical=f"{site.base_url}/{name}.html", noindex=name not in ("about",))

    _write(
        out / "static/js/config.js",
        "window.TR_CONFIG = "
        + json.dumps(
            {
                "supabaseUrl": sb_url,
                "supabaseAnonKey": sb_key,
                "adminGithub": site.admin_github,
                "boards": [b.__dict__ for b in site.boards],
            },
            ensure_ascii=False,
        )
        + ";\n",
    )
    _write(out / "search.json", json.dumps(
        [{"t": a.title, "s": a.summary[:80], "u": f"/t/{a.slug}.html", "b": a.board, "d": a.created[:10]} for a in articles[:3000]],
        ensure_ascii=False, separators=(",", ":"),
    ))
    _write_seo_files(out, site, articles, user_threads, now)
    _write(out / "CNAME", site.base_url.split("//", 1)[-1] + "\n")
    _write(out / ".nojekyll", "")
    log.info("built %d articles, %d reader threads into %s", len(articles), len(user_threads), out)
    return out


def _write_seo_files(out: Path, site: SiteConfig, articles: list[Article], user_threads: list[dict], now: str) -> None:
    base = site.base_url
    urls: list[tuple[str, str, str]] = [(base + "/", now, "hourly")]
    for b in site.boards:
        urls.append((f"{base}/f/{b.id}/", now, "hourly"))
    for a in articles:
        urls.append((f"{base}/t/{a.slug}.html", a.created, "weekly"))
    for t in user_threads:
        urls.append((f"{base}/p/{t['id']}.html", t.get("updated_at") or t.get("created_at") or now, "daily"))
    urls.append((f"{base}/about.html", now, "monthly"))

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod, freq in urls[:50000]:
        lines.append(f"<url><loc>{xml_escape(loc)}</loc><lastmod>{lastmod[:10]}</lastmod><changefreq>{freq}</changefreq></url>")
    lines.append("</urlset>")
    _write(out / "sitemap.xml", "\n".join(lines))

    # Google News sitemap: only articles from the last 2 days
    cutoff = datetime.now(timezone.utc).timestamp() - 2 * 86400
    news = [a for a in articles if datetime.fromisoformat(a.created).timestamp() >= cutoff][:1000]
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">',
    ]
    for a in news:
        lines.append(
            f"<url><loc>{xml_escape(f'{base}/t/{a.slug}.html')}</loc><news:news><news:publication>"
            f"<news:name>{xml_escape(site.short_name)}</news:name><news:language>zh-cn</news:language>"
            f"</news:publication><news:publication_date>{a.created}</news:publication_date>"
            f"<news:title>{xml_escape(a.title)}</news:title></news:news></url>"
        )
    lines.append("</urlset>")
    _write(out / "sitemap-news.xml", "\n".join(lines))

    _write(
        out / "robots.txt",
        "User-agent: *\nAllow: /\nDisallow: /login.html\nDisallow: /new.html\n\n"
        f"Sitemap: {base}/sitemap.xml\nSitemap: {base}/sitemap-news.xml\n",
    )

    items = []
    for a in articles[:50]:
        link = f"{base}/t/{a.slug}.html"
        pub = datetime.fromisoformat(a.created).strftime("%a, %d %b %Y %H:%M:%S +0000")
        items.append(
            f"<item><title>{xml_escape(a.title)}</title><link>{link}</link><guid>{link}</guid>"
            f"<pubDate>{pub}</pubDate><description>{xml_escape(a.summary)}</description></item>"
        )
    _write(
        out / "feed.xml",
        '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
        f"<title>{xml_escape(site.name)}</title><link>{base}/</link>"
        f"<description>{xml_escape(site.description)}</description><language>zh-cn</language>"
        + "".join(items)
        + "</channel></rss>",
    )
