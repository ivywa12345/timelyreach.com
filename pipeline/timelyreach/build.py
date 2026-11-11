"""Render the static site into _site/ (deployed to GitHub Pages)."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from .config import OUTPUT_DIR, SITE_SRC_DIR, SiteConfig, load_site
from .models import Article
from .store import Store
from .supabase_read import load_user_threads, load_view_stats, supabase_settings

log = logging.getLogger(__name__)


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(SITE_SRC_DIR / "templates"),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["date_cn"] = lambda iso: iso[:10] if iso else ""
    # RSS summaries (tweets especially) carry bare t.co links that read as noise in a dek
    env.filters["nourl"] = lambda text: re.sub(r"\s*https?://\S+", "", text or "").strip(" —-")
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



def _ga_id(site) -> str:
    """GA4 measurement id for the templates; anything that is not G-XXXXXXXX is ignored."""
    gid = (os.environ.get("GA_MEASUREMENT_ID") or site.ga_measurement_id or "").strip()
    return gid if re.fullmatch(r"G-[A-Z0-9]{6,12}", gid) else ""


def _gtm_id(site) -> str:
    gid = (os.environ.get("GTM_ID") or site.gtm_id or "").strip()
    return gid if re.fullmatch(r"GTM-[A-Z0-9]{4,10}", gid) else ""


def _ads(site) -> dict:
    """Ad settings for the templates. Only a well-formed publisher id turns ads on, and only
    all-digit slot ids are used, so a typo can never inject markup."""
    client = (os.environ.get("ADSENSE_CLIENT") or site.adsense_client or "").strip()
    if not re.fullmatch(r"ca-pub-\d{10,20}", client):
        return {"client": "", "slots": {}}
    slots = {k: str(v) for k, v in (site.ad_slots or {}).items() if re.fullmatch(r"\d{6,20}", str(v or ""))}
    return {"client": client, "slots": slots}


def _latest(articles: list[Article], skip: set[str], n: int = 6) -> list[Article]:
    """Newest posts not already on the front page; fills the left column next to the tall right rail."""
    fresh = [a for a in articles if a.key not in skip and a.board not in ("brief", "picks")]
    return sorted(fresh, key=lambda a: a.created, reverse=True)[:n]


HOT_DAYS = 4
HOT_COUNT = 5
REPLY_WEIGHT = 5  # a comment says more than a view
HOT_MIN_SCORE = 30  # a handful of views (mostly the owner's own) is not "hot"; below this the list is plain newest-first


def order_for_board(items: list[Article], stats: dict[str, tuple[int, int]]) -> list[Article]:
    """The most-read pieces of the last four days first (top five), then everything else newest first."""
    since = _days_ago(HOT_DAYS)

    def score(a: Article) -> int:
        views, replies = stats.get(a.thread_key, (0, 0))
        return views + REPLY_WEIGHT * replies

    hot = sorted((a for a in items if a.created >= since and score(a) >= HOT_MIN_SCORE), key=lambda a: (score(a), a.created), reverse=True)[:HOT_COUNT]
    hot_keys = {a.key for a in hot}
    return hot + sorted((a for a in items if a.key not in hot_keys), key=lambda a: a.created, reverse=True)


PICK_HOURS = 12  # an owner's pick leads the front page only while it is fresh


def _front_page(articles: list[Article], n: int = 7) -> list[Article]:
    """Today's news first: the owner's picks only while fresh (at most two), then finance and US news
    alternating, newest first. Older picks keep their own box in the right rail."""
    picks = [a for a in articles if a.source_id == "editor" and a.created >= _hours_ago(PICK_HOURS)][:2]
    taken = {a.key for a in picks}  # a pick must not come round again in the alternating lists
    finance = [a for a in articles if a.board == "finance" and a.key not in taken]
    news = [a for a in articles if a.board == "news" and a.key not in taken]
    mixed = [a for pair in zip(finance, news) for a in pair]
    mixed += finance[len(news):] + news[len(finance):]
    return (picks + mixed)[:n]


def _hours_ago(hours: int) -> str:
    from datetime import timedelta

    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def _days_ago(days: int) -> str:
    from datetime import timedelta

    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def board_page_url(board_id: str, page: int) -> str:
    return f"/f/{board_id}/" if page == 1 else f"/f/{board_id}/{page}"  # public URL; the file on disk is {page}.html


def build_site(out: Path = OUTPUT_DIR, store: Store | None = None, user_threads: list[dict] | None = None,
               stats: dict[str, tuple[int, int]] | None = None) -> Path:
    site = load_site()
    store = store or Store()
    articles = store.load_articles()
    if user_threads is None:
        user_threads = load_user_threads()
    if stats is None:
        stats = load_view_stats()
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
        # Cloudflare Web Analytics site token (public by design); no token, no beacon
        "ads": _ads(site),
        "ga_id": _ga_id(site),
        "gtm_id": _gtm_id(site),
        "analytics_token": re.sub(r"[^0-9a-zA-Z]", "", os.environ.get("CF_ANALYTICS_TOKEN") or site.analytics_token),
    }

    def render(template: str, dest: str, **ctx) -> None:
        _write(out / dest, env.get_template(template).render(**common, **ctx))

    by_board: dict[str, list[Article]] = {b.id: [] for b in site.boards}
    for a in articles:
        by_board.setdefault(a.board, []).append(a)
    # the daily brief is a dated series: always strictly newest first
    by_board = {b: (items if b == "brief" else order_for_board(items, stats)) for b, items in by_board.items()}

    # Home page
    headlines = _front_page(articles)
    render(
        "index.html",
        "index.html",
        canonical=site.base_url + "/",
        headlines=headlines,
        latest=_latest(articles, {a.key for a in headlines}),
        by_board={k: v[:12] for k, v in by_board.items()},
        latest_users=user_threads[:60],
    )

    # Board list pages
    for board in site.boards:
        items = by_board.get(board.id, [])
        pages = _paginate(items, site.per_page)
        for n, chunk in enumerate(pages, start=1):
            url = board_page_url(board.id, n)
            render(
                "board.html",
                url.lstrip("/") + ("index.html" if url.endswith("/") else ".html"),
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
            canonical=f"{site.base_url}/t/{a.slug}",
            a=a,
            board=site.board_map.get(a.board),
            related=related,
        )

    # Reader threads (static copies for search engines; comments load live)
    for t in user_threads:
        render(
            "user_thread.html",
            f"p/{t['id']}.html",
            canonical=f"{site.base_url}/p/{t['id']}",
            t=t,
            board=site.board_map.get(t.get("board")),
        )

    # App pages (dynamic, rendered in the browser)
    for name in ("thread", "login", "new", "user", "search", "about", "privacy", "404", "admin", "analytics"):
        render(f"{name}.html", f"{name}.html", canonical=f"{site.base_url}/{name}", noindex=name not in ("about", "privacy"))

    _write(
        out / "static/js/config.js",
        "window.TR_CONFIG = "
        + json.dumps(
            {
                "supabaseUrl": sb_url,
                "supabaseAnonKey": sb_key,
                "adminGithub": site.admin_github,
                "repo": "ivywa12345/timelyreach.com",
                "boards": [b.__dict__ for b in site.boards],
            },
            ensure_ascii=False,
        )
        + ";\n",
    )
    _write(out / "search.json", json.dumps(
        [{"t": a.title, "s": a.summary[:80], "u": f"/t/{a.slug}", "b": a.board, "d": a.created[:10]} for a in articles[:3000]],
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
        urls.append((f"{base}/t/{a.slug}", a.created, "weekly"))
    for t in user_threads:
        urls.append((f"{base}/p/{t['id']}", t.get("updated_at") or t.get("created_at") or now, "daily"))
    urls.append((f"{base}/about", now, "monthly"))

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
            f"<url><loc>{xml_escape(f'{base}/t/{a.slug}')}</loc><news:news><news:publication>"
            f"<news:name>{xml_escape(site.short_name)}</news:name><news:language>zh-cn</news:language>"
            f"</news:publication><news:publication_date>{a.created}</news:publication_date>"
            f"<news:title>{xml_escape(a.title)}</news:title></news:news></url>"
        )
    lines.append("</urlset>")
    _write(out / "sitemap-news.xml", "\n".join(lines))

    ads = _ads(site)
    if ads["client"]:  # AdSense requires this file at the site root
        _write(out / "ads.txt", f"google.com, {ads['client'].replace('ca-', '')}, DIRECT, f08c47fec0942fa0\n")
    _write(
        out / "robots.txt",
        "User-agent: *\nAllow: /\nDisallow: /login\nDisallow: /new\nDisallow: /admin\nDisallow: /analytics\n\n"
        f"Sitemap: {base}/sitemap.xml\nSitemap: {base}/sitemap-news.xml\n",
    )

    items = []
    for a in articles[:50]:
        link = f"{base}/t/{a.slug}"
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
