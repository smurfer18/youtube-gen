"""Claude script writer + fact checker (Anthropic Python SDK, structured outputs).

The writer returns the whole storyboard-ready script in one structured call:
chapters, narration beats, a footage query per beat, motion-graphic specs filled
from the narration's own figures, lower thirds, chapter moods and the YouTube
pack text. A second pass checks every claim against the research documents and
patches beats that are not supported.
"""

from __future__ import annotations

import json
from typing import Any

from ytgen.config import Settings
from ytgen.models import Script
from ytgen.research import Research
from ytgen.util import log
from ytgen.writers import register

L = log.get("writer")

MOODS = ["tension", "wonder", "somber", "driving", "uplifting", "neutral"]
GRAPHICS = ["big_number", "bar_chart", "line_chart", "timeline", "list", "quote", "compare", "kinetic"]


def available(settings: Settings) -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(settings.anthropic_api_key)


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


GRAPHIC_SCHEMA = _obj({
    "type": {"type": "string", "enum": GRAPHICS},
    "title": {"type": "string"},
    "value": _nullable({"type": "number"}),
    "display": _nullable({"type": "string"}),
    "prefix": {"type": "string"},
    "suffix": {"type": "string"},
    "label": {"type": "string"},
    "unit": {"type": "string"},
    "source": {"type": "string"},
    "anchor": {"type": "string"},
    "items": {"type": "array", "items": _obj({
        "label": {"type": "string"},
        "value": _nullable({"type": "number"}),
        "display": _nullable({"type": "string"}),
    })},
})

SCRIPT_SCHEMA = _obj({
    "title": {"type": "string"},
    "title_options": {"type": "array", "items": {"type": "string"}},
    "thumbnail_text": {"type": "string"},
    "description": {"type": "string"},
    "tags": {"type": "array", "items": {"type": "string"}},
    "chapters": {"type": "array", "items": _obj({
        "title": {"type": "string"},
        "mood": {"type": "string", "enum": MOODS},
        "beats": {"type": "array", "items": _obj({
            "text": {"type": "string"},
            "visual": {"type": "string"},
            "lower_third": _nullable({"type": "string"}),
            "emphasis": {"type": "array", "items": {"type": "string"}},
            "graphic": _nullable(GRAPHIC_SCHEMA),
        })},
    })},
    "sources": {"type": "array", "items": _obj({"title": {"type": "string"}, "url": {"type": "string"}})},
})

BEAT_SCHEMA = _obj({
    "text": {"type": "string"},
    "visual": {"type": "string"},
    "lower_third": _nullable({"type": "string"}),
    "emphasis": {"type": "array", "items": {"type": "string"}},
    "graphic": _nullable(GRAPHIC_SCHEMA),
})

OUTLINE_SCHEMA = _obj({
    "title": {"type": "string"},
    "title_options": {"type": "array", "items": {"type": "string"}},
    "thumbnail_text": {"type": "string"},
    "description": {"type": "string"},
    "tags": {"type": "array", "items": {"type": "string"}},
    "chapters": {"type": "array", "items": _obj({
        "title": {"type": "string"},
        "mood": {"type": "string", "enum": MOODS},
        "purpose": {"type": "string"},
        "key_facts": {"type": "array", "items": {"type": "string"}},
        "ends_on": {"type": "string"},
        "words": {"type": "integer"},
    })},
    "sources": {"type": "array", "items": _obj({"title": {"type": "string"}, "url": {"type": "string"}})},
})

CHAPTER_SCHEMA = _obj({"beats": {"type": "array", "items": BEAT_SCHEMA}, "summary": {"type": "string"}})

FIX_SCHEMA = _obj({
    "fixes": {"type": "array", "items": _obj({
        "chapter": {"type": "integer"},
        "beat": {"type": "integer"},
        "problem": {"type": "string"},
        "corrected_text": {"type": "string"},
    })},
})

SYSTEM = """You are the head writer of a top faceless YouTube documentary channel. You write
narration that keeps viewers watching to the end, and you storyboard it for an automated
editor that cuts footage and motion graphics to the narrator's words.

Writing rules:
- Open with a hook in the first 2 sentences: a concrete, surprising moment or question. No
  "In this video", no greetings, no "Welcome back".
- Tell it as a story: stakes, tension, turning points, payoff. Each chapter ends on an open
  loop that pulls into the next.
- Spoken style: short sentences mixed with longer ones, active voice, concrete nouns, numbers
  people can picture. Avoid lists of adjectives and filler like "fascinating" or "delve".
- Write numbers the way the narrator should read them (e.g. "205,000 miles", "1970").
- Every factual claim must be supported by the provided research documents when documents are
  given. Never invent quotes, dates or figures. If unsure, leave it out.
- The final beat is a short, natural call to action (subscribe / next video) of one sentence.

Storyboard rules (each beat is one paragraph of 1-4 sentences, roughly 8-25 seconds spoken):
- visual: a concrete stock/archive photo search query (2-5 words) showing what the beat is
  about: subjects, places, objects, eras. Prefer things that exist in public archives
  (e.g. "Apollo 13 service module", "Mission Control Houston 1970"). Never abstract ideas.
- graphic: add one only where the narration states a figure, a comparison, a sequence of
  dates, a ranking/list, or a memorable quote. Fill it ONLY with numbers and words the beat
  itself says. Types: big_number (value + display + label), bar_chart / line_chart (items with
  values, a unit), timeline (items: label = year/date, display = event), list (items: label),
  compare (exactly 2 items with values or displays), quote (title = quote, label = who),
  kinetic (3-6 punchy keywords as items, for an abstract beat with no good footage).
  anchor = the word in the narration where the graphic should appear. Use graphics on about
  1 beat in 4; never on two consecutive beats.
- lower_third: a place/date name card like "Houston, Texas · April 1970" when the beat moves
  to a new place or time; otherwise null.
- emphasis: 1-3 words of the beat to highlight in the captions.
- mood per chapter drives the music: tension, wonder, somber, driving, uplifting or neutral.

Packaging:
- title: under 70 characters, curiosity + clarity, no clickbait lies. title_options: 3
  alternatives in different angles. thumbnail_text: 2-4 words, uppercase-friendly, that
  complement (not repeat) the title.
- description: 2 short paragraphs for YouTube (no timestamps; they are added automatically).
- tags: 10-15 search tags. sources: the documents you used (title + url)."""


def _client(settings: Settings):
    import anthropic

    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _call_json(settings: Settings, system: str, user: str, schema: dict, max_tokens: int = 64000) -> Any:
    """Streamed structured-output call with adaptive thinking and server-side refusal fallback."""
    client = _client(settings)
    kwargs: dict[str, Any] = dict(
        model=settings.claude_model,
        max_tokens=max_tokens,
        thinking={"type": "adaptive"},
        output_config={"effort": settings.claude_effort, "format": {"type": "json_schema", "schema": schema}},
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        betas=["server-side-fallback-2026-07-01"],
    )
    try:
        with client.beta.messages.stream(fallbacks="default", **kwargs) as stream:
            msg = stream.get_final_message()
    except TypeError:  # older SDK without the `fallbacks` parameter
        with client.beta.messages.stream(extra_body={"fallbacks": "default"}, **kwargs) as stream:
            msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"Claude declined the request: {getattr(msg, 'stop_details', None)}")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("Claude hit max_tokens before finishing the script; lower --minutes")
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return json.loads(text)


@register("claude")
class ClaudeWriter:
    def write(self, topic: str, research: Research, settings: Settings) -> Script:
        if settings.format == "short":
            words = 130
            shape = ("a YouTube Short (9:16) of about 45-55 seconds: one chapter, 5-7 beats of 1-2 "
                     "sentences each, hook in the first 2 seconds, fast pace, a loop-friendly ending")
        else:
            words = int(settings.target_minutes * 150)
            n_ch = max(3, min(12, round(settings.target_minutes / 2.5)))
            shape = f"a long-form documentary of about {settings.target_minutes:g} minutes with {n_ch} chapters"
        docs = research.documents() if research.articles else ""
        user = (
            f"Topic: {topic}\nLanguage: {settings.language}\n"
            f"Write {shape}, about {words} words of narration in total.\n\n"
            + (f"Research documents (the only allowed source of facts):\n\n{docs}" if docs else
               "No research documents were provided: use only well-established facts.")
        )
        if settings.format != "short" and settings.target_minutes > 12:
            return self.write_long(topic, research, settings, words, docs)
        L.info("writing with %s (%d target words)", settings.claude_model, words)
        data = _call_json(settings, SYSTEM, user, SCRIPT_SCHEMA)
        data["topic"] = topic
        data["language"] = settings.language
        script = Script.model_validate(data)
        if settings.fact_check and docs:
            script = self.fact_check(script, docs, settings)
        L.info("script: %d chapters, %d words", len(script.chapters), script.word_count())
        return script

    def write_long(self, topic: str, research: Research, settings: Settings, words: int, docs: str) -> Script:
        """Outline first, then chapter by chapter with a running memory, so 20-60 minute
        scripts keep one narrative arc and never repeat a fact or a scene."""
        n_ch = max(5, min(18, round(settings.target_minutes / 2.2)))
        src = f"Research documents (the only allowed source of facts):\n\n{docs}" if docs else \
            "No research documents: use only well-established facts."
        outline = _call_json(settings, SYSTEM, (
            f"Topic: {topic}\nLanguage: {settings.language}\nPlan a {settings.target_minutes:g}-minute documentary "
            f"(about {words} narration words) as {n_ch} chapters. The first chapter is a 60-90 second cold open "
            "that hooks with the most dramatic moment; the rest follow a single narrative arc with rising stakes, "
            "a midpoint turn and a payoff. For each chapter give its purpose in the story, the specific facts it "
            "will use (no fact may appear in two chapters), the open loop it ends on, and its word budget "
            f"(budgets must add up to about {words}).\n\n{src}"), OUTLINE_SCHEMA, 32000)
        plan_txt = "\n".join(f"{i}. {c['title']} [{c['mood']}] ({c['words']} words): {c['purpose']} | facts: "
                              f"{'; '.join(c['key_facts'])} | ends on: {c['ends_on']}" for i, c in enumerate(outline["chapters"]))
        chapters, memory, used_facts = [], [], []
        for i, ch in enumerate(outline["chapters"]):
            L.info("chapter %d/%d: %s", i + 1, len(outline["chapters"]), ch["title"])
            data = _call_json(settings, SYSTEM, (
                f"Documentary: {outline['title']}\nFull outline:\n{plan_txt}\n\n"
                f"Story so far (do not repeat any of it):\n" + ("\n".join(memory) or "(this is the opening)") +
                f"\n\nFacts already used: {'; '.join(used_facts) or 'none'}\n\n"
                f"Now write chapter {i}: '{ch['title']}' — about {ch['words']} words of narration as beats "
                f"(each 1-4 sentences with its visual query and optional graphic/lower third). Use the chapter's "
                f"planned facts, end on: {ch['ends_on']}."
                + (" This is the final chapter: land the payoff and end with a one-sentence call to subscribe."
                   if i == len(outline["chapters"]) - 1 else "")
                + f"\n\n{src}"), CHAPTER_SCHEMA, 32000)
            chapters.append({"title": ch["title"], "mood": ch["mood"], "beats": data["beats"]})
            memory.append(f"Ch.{i} {ch['title']}: {data['summary']}")
            used_facts += ch["key_facts"]
        script = Script.model_validate({**{k: outline[k] for k in ("title", "title_options", "thumbnail_text",
                                                                      "description", "tags", "sources")},
                                        "topic": topic, "language": settings.language, "chapters": chapters})
        script = dedupe(script)
        if settings.fact_check and docs:
            for ci in range(len(script.chapters)):  # chapter-sized checks keep each call focused
                part = Script(title=script.title, chapters=[script.chapters[ci]])
                fixed = self.fact_check(part, docs, settings)
                script.chapters[ci] = fixed.chapters[0]
                script.notes += [n.replace("[0.", f"[{ci}.") for n in fixed.notes]
        L.info("long-form script: %d chapters, %d words", len(script.chapters), script.word_count())
        return script

    def fact_check(self, script: Script, docs: str, settings: Settings) -> Script:
        numbered = "\n".join(
            f"[{ci}.{bi}] {b.text}" for ci, c in enumerate(script.chapters) for bi, b in enumerate(c.beats)
        )
        user = (
            "Check every factual claim (names, dates, numbers, quotes, causal claims) in these "
            "narration beats against the research documents. Return a fix only for beats that "
            "contain a claim the documents contradict or do not support; the corrected_text must "
            "keep the beat's length and tone and remove or correct only the unsupported part.\n\n"
            f"Beats:\n{numbered}\n\nResearch documents:\n\n{docs}"
        )
        L.info("fact-checking %d beats", sum(len(c.beats) for c in script.chapters))
        fixes = _call_json(settings, "You are a meticulous documentary fact checker.", user, FIX_SCHEMA, 32000)["fixes"]
        for f in fixes:
            try:
                beat = script.chapters[f["chapter"]].beats[f["beat"]]
            except (IndexError, KeyError):
                continue
            beat.text = f["corrected_text"]
            script.notes.append(f"fact-check [{f['chapter']}.{f['beat']}]: {f['problem']}")
        L.info("fact-check applied %d fixes", len(fixes))
        return script


def _shingles(text: str, n: int = 5) -> set:
    w = [x.lower().strip(".,;:!?\"'") for x in text.split()]
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def dedupe(script: Script, threshold: float = 0.5) -> Script:
    """Drop beats that largely repeat an earlier beat (5-word shingle overlap)."""
    seen: set = set()
    dropped = 0
    for ch in script.chapters:
        keep = []
        for b in ch.beats:
            sh = _shingles(b.text)
            if sh and len(sh & seen) / len(sh) > threshold:
                dropped += 1
                continue
            seen |= sh
            keep.append(b)
        ch.beats = keep or ch.beats[:1]
    if dropped:
        script.notes.append(f"dedupe: removed {dropped} repetitive beats")
    return script
