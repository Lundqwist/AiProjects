"""Web and CV tools for the voice assistant: search the web, read a page, read the user's CV.

All read-only. Search uses DuckDuckGo (ddgs, no key), pages are read with trafilatura, the same
way 04-research does it. The CV is the public page on lundqwist.se, cached for an hour.
"""
from __future__ import annotations

import json
import time
import warnings

warnings.filterwarnings("ignore", message=".*SOCKS support.*")

import trafilatura
from ddgs import DDGS

CV_URL = "https://lundqwist.se/v1/CV"
PAGE_CHARS = 3500
CV_CHARS = 6000
_cv_cache: dict[str, tuple[float, str]] = {}


def web_search(query: str) -> str:
    """Search the web (news, facts, job listings, anything current). Returns up to 6 results with
    title, url and a short snippet. Read the best results with read_page before relying on them.

    Args:
        query: a few search keywords, e.g. "remote junior .NET developer jobs Sweden"
    """
    results = DDGS().text(query, max_results=6) or []
    if not results:
        return "No results. Try other keywords."
    return json.dumps([{"title": r["title"], "url": r["href"], "snippet": r["body"]} for r in results],
                      ensure_ascii=False)


def read_page(url: str) -> str:
    """Read a web page and return its main text (shortened). Use it on a url from web_search.

    Args:
        url: the full address of the page
    """
    html = trafilatura.fetch_url(url)
    text = trafilatura.extract(html, include_comments=False) if html else None
    if not text:
        return "Could not read this page. Try another result."
    return f"{url}\n\n{text[:PAGE_CHARS]}"


def read_my_cv(language: str = "en") -> str:
    """Read the user's own CV (Bjarne Lundqwist): profile, skills, jobs, education and courses.
    Use it whenever the user asks about their CV, their experience, or which jobs would suit them.

    Args:
        language: "en" or "sv"
    """
    lang = "sv" if language.lower().startswith("sv") else "en"
    cached = _cv_cache.get(lang)
    if cached and time.time() - cached[0] < 3600:
        return cached[1]
    html = trafilatura.fetch_url(f"{CV_URL}?lang={lang}")
    text = trafilatura.extract(html, include_tables=True, favor_recall=True) if html else None
    if not text:
        raise RuntimeError(f"Could not read the CV at {CV_URL}.")
    text = f"The user's CV ({CV_URL}):\n\n{text[:CV_CHARS]}"
    _cv_cache[lang] = (time.time(), text)
    return text


TOOLS = {f.__name__: f for f in (web_search, read_page, read_my_cv)}
