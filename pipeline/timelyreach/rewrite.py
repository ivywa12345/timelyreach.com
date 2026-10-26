"""Turn raw source items into readable Chinese forum threads.

Providers, tried in this order:

1. Claude (``ANTHROPIC_API_KEY``) - natural, catchy Chinese news writing.
2. Any OpenAI-compatible endpoint (``LLM_BASE_URL`` + ``LLM_API_KEY`` + ``LLM_MODEL``),
   e.g. Google Gemini's free tier or DeepSeek.
3. Free machine translation via deep-translator (Google Translate web) - no key
   needed, but only translates; it cannot rewrite.

Articles are *compiled digests* with a link to the original, not full
translations, to stay on the right side of the sources' copyright.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

import requests
from pydantic import BaseModel, Field

from .models import Article, RawItem

log = logging.getLogger(__name__)

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")


class Rewrite(BaseModel):
    title: str = Field(description="中文标题，18-32 个汉字，吸引眼球但必须忠于事实")
    summary: str = Field(description="一两句话的中文导读，60-120 字，用于列表和搜索引擎描述")
    body: list[str] = Field(description="中文正文段落，每段 60-200 字")
    tags: list[str] = Field(description="3-6 个中文关键词标签")


SYSTEM_PROMPT = """你是一家面向海外华人的中文新闻网站的资深编辑，擅长把英文新闻改写成通俗易懂、读起来有劲的中文稿件，风格接近华人论坛里的热门帖子。

写作要求：
- 标题：抓人眼球、口语化，可以用设问、数字、反差，但不能夸大或编造原文没有的事实。
- 正文：用自己的话"编译"，不要逐句翻译。先一句话讲清楚发生了什么，再交代背景、关键数字和各方说法，最后可以点出这件事对在美华人（钱包、工作、身份、子女教育、安全等）意味着什么。
- 语言自然流畅，像中国人写的，不要翻译腔。人名、公司名首次出现时括号附英文原名；美元金额保留"美元"。
- 只使用提供的材料里的事实。材料只有标题和摘要时，就写一篇简短的快讯（2-3 段），不要脑补细节。
- 原文的直接引语可以翻译后少量引用，并注明是谁说的。
- 用简体中文。"""

ZH_SYSTEM_PROMPT = """你是海外华人论坛的版主，负责把其他论坛的热帖做成"导读"，吸引读者来讨论。

要求：
- 标题：保留原帖的意思，可以稍微润色得更吸引人，不要编造。
- 正文写 1-2 段导读：概括原帖在讨论什么、争议点在哪，结尾抛出一个问题引导大家讨论。
- 不要大段照抄原帖内容。用简体中文。"""

EDITOR_SYSTEM_PROMPT = """你是一家海外华人中文网站站长的编辑助理。站长推荐了一篇文章，并写下了自己的点评和要点。请据此写一篇"站长荐读"帖子。

要求：
- 以站长的观点为主线，用站长第一人称（"我"）的口吻，润色语言但不改变、不夸大站长的观点。
- 简要介绍原文讲了什么，只能基于提供的原文标题、摘要和站长要点，不要编造原文没有的事实或数字。
- 如果站长提供了原文摘录，最多引用两句并注明出处；不要大段转述原文。
- 标题要吸引人、口语化，体现站长的看法。
- 结尾抛出一个问题，邀请读者讨论。
- 用简体中文。"""

BRIEF_SYSTEM_PROMPT = """你是海外华人财经社区的编辑，负责每个交易日开盘前的"财经早报"。材料是过去 24 小时本站已发布的新闻标题和导读。

要求：
- 标题格式："X月X日财经早报：<今天最重要的一件事>"，口语化、抓人，但不夸张。
- 第一段用两三句话概括今天市场最该关注什么。
- 然后挑 5-8 条最重要的新闻，每条一段：先一句话说发生了什么，再一句话说对美股/利率/华人钱包可能意味着什么。只用材料里的事实，不要编造数字或行情。
- 最后一段抛出一个问题，邀请读者在跟帖里聊今天的看法，并提醒"以上不构成投资建议"。
- 用简体中文。"""

ZH_NEWS_SYSTEM_PROMPT = """你是海外华人财经社区的编辑。材料是一篇中文财经报道（来源已注明）。请"稍加改写"成本站的财经快讯：

- 标题：换一种说法，更抓人、更口语化，但意思和事实不变，不夸大。
- 正文 3-6 段，总长 300-700 字：保留关键事实、数字和各方说法，用自己的话重新组织，不要大段照抄原文句子。
- 第一段里自然地注明出处，例如"据华尔街见闻报道"，出处用材料里的"来源"。不要提文学城。
- 只用材料里的事实，不编造。可以在最后一段点出这件事对美股或海外华人投资者可能意味着什么，并抛出一个讨论问题。
- 用简体中文。"""


def _system_prompt(item: RawItem) -> str:
    return {
        "zh": ZH_SYSTEM_PROMPT,
        "zh-news": ZH_NEWS_SYSTEM_PROMPT,
        "editor": EDITOR_SYSTEM_PROMPT,
        "brief": BRIEF_SYSTEM_PROMPT,
    }.get(item.lang, SYSTEM_PROMPT)


def _material(item: RawItem) -> str:
    parts = [f"来源：{item.source_name}", f"原标题：{item.title}"]
    if item.author:
        parts.append(f"作者：{item.author}")
    if item.summary:
        parts.append(f"摘要：{item.summary}")
    if item.text:
        parts.append(f"正文：\n{item.text[:8000]}")
    return "\n".join(parts)


class Provider:
    name = "base"

    def rewrite(self, item: RawItem) -> Rewrite:
        raise NotImplementedError


class ClaudeProvider(Provider):
    name = "claude"

    def __init__(self) -> None:
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic()

    def rewrite(self, item: RawItem) -> Rewrite:
        system = _system_prompt(item)
        response = self.client.beta.messages.parse(
            model=CLAUDE_MODEL,
            max_tokens=8000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low"},
            system=system,
            messages=[{"role": "user", "content": "请根据以下材料写稿：\n\n" + _material(item)}],
            output_format=Rewrite,
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise RuntimeError(f"claude returned no usable output (stop_reason={response.stop_reason})")
        return response.parsed_output


class OpenAICompatibleProvider(Provider):
    """Gemini (https://generativelanguage.googleapis.com/v1beta/openai/), DeepSeek, etc."""

    name = "openai-compatible"

    def __init__(self) -> None:
        self.base_url = os.environ["LLM_BASE_URL"].rstrip("/")
        self.api_key = os.environ["LLM_API_KEY"]
        self.model = os.environ.get("LLM_MODEL", "gemini-2.5-flash")

    def rewrite(self, item: RawItem) -> Rewrite:
        system = _system_prompt(item)
        schema_hint = '只输出 JSON：{"title": str, "summary": str, "body": [str, ...], "tags": [str, ...]}'
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system + "\n\n" + schema_hint},
                    {"role": "user", "content": "请根据以下材料写稿：\n\n" + _material(item)},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=120,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
        return Rewrite.model_validate(json.loads(content))


class FreeTranslateProvider(Provider):
    """No-key fallback: translate title + a short excerpt with deep-translator."""

    name = "free-translate"
    google_blocked = False

    def _translate(self, text: str) -> str:
        """Google Translate web, throttled and retried; MyMemory if Google keeps rate-limiting."""
        from deep_translator import GoogleTranslator, MyMemoryTranslator

        text = text[:4500]
        if not self.google_blocked:
            for delay in (1.5, 6, 15):
                time.sleep(delay)  # Google allows ~5 req/s but blocks bursts from CI IPs
                try:
                    return GoogleTranslator(source="auto", target="zh-CN").translate(text) or ""
                except Exception as exc:  # noqa: BLE001
                    log.info("google translate retry after error: %s", exc)
            self.google_blocked = True  # don't spend 20s per call for the rest of this run
            log.warning("google translate unavailable; using MyMemory for the rest of the run")
        return MyMemoryTranslator(source="en-GB", target="zh-CN").translate(text[:480]) or ""

    def rewrite(self, item: RawItem) -> Rewrite:
        if item.lang == "brief":  # headlines are already Chinese: list them as the brief
            body = ["过去 24 小时最值得关注的财经要闻：", *item.text.split("\n"),
                    "今天你最关注哪一条？欢迎跟帖聊聊你的看法。（以上不构成投资建议）"]
            return Rewrite(title=item.title, summary=item.summary, body=body, tags=["每日简报", "美股"])
        if item.lang == "zh-news":  # Chinese report: no model to rewrite it, so keep a short excerpt with credit
            paras, total = [], 0
            for p in (item.text or item.summary).split("\n"):
                p = p.strip()
                if len(p) < 8:
                    continue
                paras.append(p)
                total += len(p)
                if total > 600 or len(paras) >= 4:
                    break
            body = paras + [f"（本文据{item.source_name}报道整理。欢迎在下面跟帖讨论！）"]
            summary = (paras[0] if paras else item.title)[:120]
            return Rewrite(title=item.title, summary=summary, body=body, tags=[item.source_name, "财经"])
        if item.lang == "editor":  # editor's own notes are already Chinese: publish them as written
            body = [p.strip() for p in item.text.split("\n") if p.strip()]
            return Rewrite(title=item.title, summary=(body[0] if body else item.title)[:120], body=body, tags=["站长荐读"])
        excerpt_src = item.summary or item.text
        sentences = re.split(r"(?<=[.!?。！？])\s+", excerpt_src)
        excerpt = " ".join(sentences[:4])[:900]
        if item.lang == "zh":
            title, excerpt_zh = item.title, excerpt
        else:
            title = self._translate(item.title)
            excerpt_zh = self._translate(excerpt) if excerpt else ""
        body = [p for p in [excerpt_zh] if p]
        body.append(f"（以上为{item.source_name}内容摘译，详情请看原文。欢迎在下面跟帖讨论！）")
        summary = (excerpt_zh or title)[:120]
        return Rewrite(title=title, summary=summary, body=body, tags=[item.source_name])


def available_providers() -> list[Provider]:
    providers: list[Provider] = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            providers.append(ClaudeProvider())
        except Exception as exc:  # noqa: BLE001
            log.warning("claude provider unavailable: %s", exc)
    if os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY"):
        providers.append(OpenAICompatibleProvider())
    if os.environ.get("TR_DISABLE_FREE_TRANSLATE") != "1":
        providers.append(FreeTranslateProvider())
    return providers


def to_article(item: RawItem, providers: list[Provider]) -> Article | None:
    for provider in list(providers):
        try:
            rw = provider.rewrite(item)
        except Exception as exc:  # noqa: BLE001 - fall through to the next provider
            log.warning("%s failed on %s: %s", provider.name, item.url, exc)
            if _is_auth_error(exc):
                providers.remove(provider)  # bad key: stop retrying it for this run
            continue
        body = [p.strip() for p in rw.body if p and p.strip()]
        if not rw.title.strip() or not body:
            continue
        return Article(
            key=item.key,
            board=item.board,
            source_id=item.source_id,
            source_name=item.source_name,
            source_url=item.url,
            title=rw.title.strip(),
            summary=rw.summary.strip(),
            body=body,
            tags=[t.strip() for t in rw.tags if t.strip()][:6],
            original_title=item.title,
            lang=item.lang,
            published=item.published,
            rewriter=provider.name,
            show_link=item.show_link,
        )
    return None


def _is_auth_error(exc: Exception) -> bool:
    try:
        import anthropic

        if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            return True
    except ImportError:
        pass
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code in (401, 403)
    return False
