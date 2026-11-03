"""Topic research from Wikipedia: grounded facts, sections and citations.

Used to ground LLM writers (every figure must come from a source) and as the
raw material of the offline extractive writer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ytgen.util import log, net

L = log.get("research")
API = "https://{lang}.wikipedia.org/w/api.php"


@dataclass
class Section:
    heading: str
    text: str


@dataclass
class Article:
    title: str
    url: str
    summary: str
    sections: list[Section] = field(default_factory=list)

    def as_document(self, max_chars: int = 40000) -> str:
        parts = [f"# {self.title}\nSource: {self.url}\n\n{self.summary}"]
        for s in self.sections:
            parts.append(f"## {s.heading}\n{s.text}")
        return "\n\n".join(parts)[:max_chars]


@dataclass
class Research:
    topic: str
    articles: list[Article] = field(default_factory=list)

    def documents(self, max_chars: int = 60000) -> str:
        out, used = [], 0
        for a in self.articles:
            d = a.as_document(max_chars - used)
            out.append(d)
            used += len(d)
            if used >= max_chars:
                break
        return "\n\n---\n\n".join(out)


_SKIP = {"see also", "references", "notes", "external links", "further reading", "bibliography",
         "sources", "citations", "footnotes", "gallery", "in popular culture", "legacy and media"}


def _get(params: dict, lang: str) -> dict:
    """Paced Wikipedia API call; waits out a 429 once (Wikipedia asks for <1 req/s bursts)."""
    import time

    for attempt in range(3):
        net.pace(f"{lang}.wikipedia.org", 1.0)
        try:
            return net.get_json(API.format(lang=lang), params=params, timeout=25)
        except net.Throttled as e:
            if attempt == 2:
                raise
            time.sleep(min(60.0, e.retry_after + 1))
    return {}


def search_titles(query: str, lang: str = "en", limit: int = 3) -> list[str]:
    d = _get({
        "action": "query", "list": "search", "srsearch": query, "srlimit": limit,
        "format": "json", "srprop": "",
    }, lang)
    return [h["title"] for h in d.get("query", {}).get("search", [])]


def fetch_article(title: str, lang: str = "en") -> Article:
    d = _get({
        "action": "query", "prop": "extracts|info", "titles": title, "explaintext": 1,
        "exsectionformat": "wiki", "inprop": "url", "redirects": 1, "format": "json",
    }, lang)
    page = next(iter(d["query"]["pages"].values()))
    text = page.get("extract", "")
    url = page.get("fullurl", f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}")
    chunks = re.split(r"\n(={2,3})\s*(.+?)\s*\1\n", text)
    summary = chunks[0].strip()
    sections: list[Section] = []
    for i in range(1, len(chunks) - 2, 3):
        heading, body = chunks[i + 1].strip(), chunks[i + 2].strip()
        if heading.lower() in _SKIP or len(body) < 200:
            continue
        sections.append(Section(heading, re.sub(r"\n{2,}", "\n", body)))
    return Article(title=page.get("title", title), url=url, summary=summary, sections=sections)


def research(topic: str, lang: str = "en", max_articles: int = 2) -> Research:
    r = Research(topic=topic)
    try:
        titles = search_titles(topic, lang, limit=max_articles)
    except Exception as e:  # network optional
        L.warning("wikipedia search failed: %s", e)
        return r
    for t in titles:
        try:
            a = fetch_article(t, lang)
            if len(a.summary) > 200:
                r.articles.append(a)
                L.info("article: %s (%d sections)", a.title, len(a.sections))
        except Exception as e:
            L.warning("fetch %s failed: %s", t, e)
    return r
