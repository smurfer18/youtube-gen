"""Offline / keyless writer: builds a narrated script from Wikipedia research.

It does not paraphrase. It selects, cleans and orders sentences from the
articles (summary as the cold open, then the richest sections as chapters),
so every statement is sourced by construction. Quality is below an LLM writer;
use it when no API key is configured, or as a factual skeleton to edit.
"""

from __future__ import annotations

import re

from ytgen.config import Settings
from ytgen.models import Beat, Chapter, Script, Source
from ytgen.research import Research
from ytgen.util import log
from ytgen.util.text import keywords, split_sentences
from ytgen.writers import register

L = log.get("writer")

_MOOD_WORDS = {
    "tension": "war battle attack crime murder disaster explosion crisis killed fire siege escape danger fail",
    "somber": "death died tragedy collapse decline famine plague loss mourning victims",
    "wonder": "space universe planet star galaxy discovery ocean ancient mystery nature evolution",
    "driving": "race speed engine company growth market empire build record",
    "uplifting": "rescue saved triumph success celebrate peace returned",
}


def _mood(text: str) -> str:
    t = text.lower()
    scores = {m: sum(t.count(w) for w in ws.split()) for m, ws in _MOOD_WORDS.items()}
    m = max(scores, key=scores.get)
    return m if scores[m] > 0 else "neutral"


def _clean(s: str) -> str:
    s = re.sub(r"\s*\([^()]*\)", "", s)        # parentheticals (pronunciations, dates in brackets)
    s = re.sub(r"\[[^\]]*\]", "", s)           # citation residue
    s = re.sub(r"\s+([,.;:])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def _good(s: str) -> bool:
    n = len(s.split())
    return 6 <= n <= 38 and not re.search(r"[{}=|]|ISBN|doi:|https?://", s)


def _beats(sentences: list[str], topic: str, per: int = 2) -> list[Beat]:
    out = []
    for i in range(0, len(sentences), per):
        group = sentences[i:i + per]
        text = " ".join(group)
        kw = keywords(text, 3)
        visual = " ".join(kw[:2]) if kw else topic
        if topic.lower() not in visual.lower() and len(visual.split()) < 3:
            visual = f"{topic} {visual}"
        out.append(Beat(text=text, visual=visual))
    return out


@register("extractive")
class ExtractiveWriter:
    def write(self, topic: str, research: Research, settings: Settings) -> Script:
        if not research.articles:
            raise RuntimeError(
                "The extractive writer needs Wikipedia research (network) or use --script "
                "to supply your own script, or set ANTHROPIC_API_KEY for the Claude writer."
            )
        art = research.articles[0]
        budget = 120 if settings.format == "short" else int(settings.target_minutes * 150)
        used = 0
        chapters: list[Chapter] = []

        intro = [_clean(s) for s in split_sentences(art.summary)]
        intro = [s for s in intro if _good(s)][: (3 if settings.format == "short" else 5)]
        chapters.append(Chapter(title=art.title, mood=_mood(" ".join(intro)), beats=_beats(intro, art.title)))
        used += sum(len(s.split()) for s in intro)

        if settings.format != "short":
            ranked = sorted(art.sections, key=lambda s: len(s.text), reverse=True)[:8]
            keep = [s for s in art.sections if s in ranked]
            per_ch = max(60, (budget - used) // max(1, len(keep)))
            for sec in keep:
                if used >= budget:
                    break
                sents, words = [], 0
                for s in split_sentences(sec.text):
                    s = _clean(s)
                    if not _good(s):
                        continue
                    sents.append(s)
                    words += len(s.split())
                    if words >= per_ch:
                        break
                if len(sents) < 2:
                    continue
                chapters.append(Chapter(title=sec.heading, mood=_mood(" ".join(sents)), beats=_beats(sents, art.title)))
                used += words

        chapters[-1].beats.append(Beat(
            text="If you enjoyed this story, subscribe for more documentaries like it.",
            visual=f"{art.title}",
        ))
        title = art.title
        script = Script(
            title=f"The Story of {title}" if len(title) < 50 else title,
            topic=topic,
            language=settings.language,
            title_options=[f"{title}: What Really Happened", f"{title}, Explained", f"The Untold Story of {title}"],
            thumbnail_text=" ".join(title.split()[:3]).upper(),
            description=_clean(" ".join(split_sentences(art.summary)[:2])),
            tags=[title.lower(), topic.lower(), "documentary", "history", "explained"],
            chapters=chapters,
            sources=[Source(title=a.title, url=a.url) for a in research.articles],
            notes=["extractive writer: narration is quoted/condensed from the listed sources"],
        )
        L.info("extractive script: %d chapters, %d words", len(chapters), script.word_count())
        return script
