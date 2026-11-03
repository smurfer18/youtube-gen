"""Scene planner: Script + word clock -> timed storyboard (Plan).

Pacing rules:
* cuts land a few frames before the word that starts the new shot;
* shot length varies (long-form 3.5-7 s, Shorts 1.6-3 s), snapped to
  sentence starts, so the edit breathes with the narration;
* a beat with a graphic becomes a graphic scene whose internal beats (counter
  start, bar reveals, list items) fire on the words that say them;
* graphics are auto-detected from the narration (figures, year sequences) when
  the writer did not declare them, at most one per few beats;
* chapter cards, lower thirds, a subscribe banner and an outro are placed from
  the structure; SFX and music sections follow the same clock.
"""

from __future__ import annotations

import random
import re
from typing import Optional

from ytgen.config import Settings
from ytgen.models import (CaptionPhrase, CaptionWord, ChapterMark, GraphicItem, GraphicSpec,
                          Motion, MusicSection, Plan, Scene, Script, SfxCue, Timing, Word)
from ytgen.util.text import STOPWORDS, keywords, norm_key

CUT_LEAD = 0.08  # cut this many seconds before the first word of a shot

_YEAR = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
_FIG = re.compile(
    r"(?P<pre>\$|€|£)?(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?:\s*(?P<mult>million|billion|trillion|thousand))?"
    r"(?P<pct>\s?%|\s?percent)?(?P<rest>(?:\s+[A-Za-z][\w'-]*){0,4})"
)
_UNITS = ("miles", "kilometers", "km", "feet", "meters", "people", "years", "hours", "days", "tons", "tonnes",
          "dollars", "pounds", "degrees", "men", "women", "soldiers", "lives", "ships", "times", "minutes",
          "seconds", "mph", "percent", "metres", "kilograms", "workers", "victims", "deaths", "acres")


def detect_graphic(text: str) -> Optional[GraphicSpec]:
    """Heuristic graphic from narration: a striking figure or a run of years."""
    years = _YEAR.findall(text)
    if len(set(years)) >= 3:
        items = []
        for m in _YEAR.finditer(text):
            after = re.split(r"[,.;:]", text[m.end():])[0].strip()
            ev = " ".join(after.split()[:6])
            if ev and m.group(1) not in [i.label for i in items]:
                items.append(GraphicItem(label=m.group(1), display=ev[:48]))
        if len(items) >= 3:
            return GraphicSpec(type="timeline", items=items[:5], anchor=items[0].label)
    best = None
    for m in _FIG.finditer(text):
        num = m.group("num")
        val = float(num.replace(",", ""))
        if _YEAR.fullmatch(num) and not m.group("pre") and not m.group("pct"):
            continue  # a bare year is a date, not a figure
        rest_words = (m.group("rest") or "").split()
        unit = rest_words[0].lower() if rest_words else ""
        striking = bool(m.group("pre") or m.group("pct") or m.group("mult")) or (val >= 1000) or (unit in _UNITS and val >= 10)
        if not striking:
            continue
        suffix = ""
        if m.group("mult"):
            suffix += " " + m.group("mult")
        if m.group("pct"):
            suffix += "%"
        label_words = rest_words[:4]
        while label_words and label_words[-1].lower() in STOPWORDS:
            label_words.pop()
        label = " ".join(label_words).strip(" ,.")
        score = len(num) + (3 if m.group("pre") or m.group("pct") else 0)
        if best is None or score > best[0]:
            best = (score, GraphicSpec(type="big_number", value=val, display=(m.group("pre") or "") + num + suffix,
                                       prefix=m.group("pre") or "", suffix=suffix, label=label,
                                       anchor=num.split(",")[0]))
    return best[1] if best else None


def _word_time(words: list[Word], needle: str, after: float = -1) -> Optional[float]:
    k = norm_key(needle)
    if not k:
        return None
    for w in words:
        if w.start >= after and (norm_key(w.text).startswith(k) or k in norm_key(w.text)):
            return w.start
    return None


def _phrases(words: list[Word], vertical: bool, emphasis: set[str]) -> list[CaptionPhrase]:
    max_words, max_chars = (3, 16) if vertical else (7, 38)
    out: list[CaptionPhrase] = []
    cur: list[Word] = []

    def flush():
        if cur:
            out.append(CaptionPhrase(start=cur[0].start, end=cur[-1].end, words=[
                CaptionWord(text=w.text, start=w.start, end=w.end,
                            emphasis=norm_key(w.text) in emphasis or bool(re.search(r"\d", w.text)))
                for w in cur]))
            cur.clear()

    for i, w in enumerate(words):
        if cur:
            gap = w.start - cur[-1].end
            chars = sum(len(x.text) + 1 for x in cur) + len(w.text)
            if (len(cur) >= max_words or chars > max_chars or gap > 0.45 or w.sentence != cur[-1].sentence
                    or re.search(r"[,;:—–-]$", cur[-1].text) and len(cur) >= 2):
                flush()
        cur.append(w)
    flush()
    for a, b in zip(out, out[1:]):  # hold each phrase until the next one starts (no flicker)
        if b.start - a.end < 0.6:
            a.end = b.start
        else:
            a.end = a.end + 0.35
    if out:
        out[-1].end += 0.5
    return out


def _motion(i: int, rng: random.Random) -> Motion:
    zin = i % 2 == 0
    z0, z1 = (1.0, rng.uniform(1.08, 1.16)) if zin else (rng.uniform(1.10, 1.16), 1.0)
    dx = rng.choice([-1, 1]) * rng.uniform(0.04, 0.10)
    dy = rng.uniform(-0.04, 0.04)
    return Motion(zoom_from=round(z0, 3), zoom_to=round(z1, 3),
                  pan_from=(round(0.5 - dx / 2, 3), round(0.5 - dy / 2, 3)),
                  pan_to=(round(0.5 + dx / 2, 3), round(0.5 + dy / 2, 3)))


_CTX_SFX = [
    (re.compile(r"^(explo\w*|blast\w*|bang|boom|detonat\w*|crash\w*)$", re.I), "impact", -7),
    (re.compile(r"^(radio\w*|transmi\w*|houston|mayday|signal\w*)$", re.I), "radio", -12),
    (re.compile(r"^(launch\w*|liftoff|lifted|rocket\w*|engines?|thunder\w*|roar\w*)$", re.I), "rumble", -8),
    (re.compile(r"^(alarm\w*|warning|sirens?|alert\w*)$", re.I), "alarm", -14),
    (re.compile(r"^(countdown|clock|ticking|seconds)$", re.I), "tick", -10),
    (re.compile(r"^(camera|photograph\w*|snapshot|photo)$", re.I), "shutter", -10),
]


def _pick(weights: dict, rng: random.Random) -> str:
    items = list(weights.items())
    tot = sum(w for _, w in items) or 1
    r = rng.random() * tot
    for k, w in items:
        r -= w
        if r <= 0:
            return k
    return items[-1][0]


def place_name(lower_third: str) -> str:
    return re.split(r"\s[·|•–-]\s|·", lower_third)[0].strip()


def plan(script: Script, timing: Timing, settings: Settings, theme: dict, preset: Optional[dict] = None) -> Plan:
    from ytgen import presets as presets_mod

    preset = preset or presets_mod.load("shorts" if settings.vertical else settings.preset_name)
    rng = random.Random(hash(script.title) & 0xFFFF)
    vertical = settings.vertical
    words = timing.words
    shot_lo, shot_hi = preset.get("shot_seconds", [3.6, 7.0])
    if vertical and preset.get("name") != "shorts":
        shot_lo, shot_hi = shot_lo * 0.5, shot_hi * 0.5
    g_max = preset.get("graphic_max_seconds", 9.5)
    g_gap = preset.get("graphic_gap_beats", 3)
    trans_mix = preset.get("transitions", {"crossfade": 1.0})
    beats = [(ci, b) for ci, c in enumerate(script.chapters) for b in c.beats]
    by_beat: dict[int, list[Word]] = {}
    for w in words:
        by_beat.setdefault(w.beat, []).append(w)
    beat_ids = [gi for gi in range(len(beats)) if gi in by_beat]

    # cut point that starts each beat (the end is the next beat's start)
    starts = {gi: (0.0 if k == 0 else max(0.0, by_beat[gi][0].start - CUT_LEAD)) for k, gi in enumerate(beat_ids)}
    ends = {gi: (starts[beat_ids[k + 1]] if k + 1 < len(beat_ids) else timing.duration) for k, gi in enumerate(beat_ids)}

    scenes: list[Scene] = []
    sfx: list[SfxCue] = []
    last_graphic_beat = -10
    auto_ok = preset.get("auto_graphics", True) and not any(b.graphic for _, b in beats)
    topic_kw = script.topic or script.title

    def add(kind, s, e, **kw) -> Scene:
        sc = Scene(index=len(scenes), kind=kind, start=round(s, 3), end=round(e, 3), **kw)
        scenes.append(sc)
        return sc

    for k, gi in enumerate(beat_ids):
        ci, beat = beats[gi]
        bw = by_beat[gi]
        s, e = starts[gi], ends[gi]
        text = beat.text
        g = beat.graphic
        if g is None and auto_ok and gi - last_graphic_beat >= g_gap and k not in (0, len(beat_ids) - 1):
            g = detect_graphic(text)
        query = beat.visual or " ".join(keywords(text, 3)) or topic_kw
        cursor = s
        first_scene_of_beat = None
        lt_target = None  # scene that carries this beat's lower third

        # a new place: fly the globe there before the footage (documentary maps)
        if (beat.lower_third and preset.get("maps") and g is None and not vertical and e - s > 6.0
                and place_name(beat.lower_third)):
            m_end = min(e - 2.5, s + 4.2)
            add("html", s, m_end, chapter=ci, text=text, query=query, transition="crossfade",
                graphic=GraphicSpec(type="map", label=place_name(beat.lower_third), title=beat.lower_third))
            sfx.append(SfxCue(t=s + 0.2, kind="whoosh", gain_db=-8))
            cursor = m_end

        if g is not None:
            last_graphic_beat = gi
            # graphic covers the sentence(s) that state it, at least 3 s
            anchor_t = _word_time(bw, g.anchor) if g.anchor else None
            g_start = cursor
            if anchor_t is not None and anchor_t - cursor > 4.5:   # figure is late in a long beat: footage first
                g_start = max(cursor, anchor_t - 1.2)
                sc = add("footage", cursor, g_start, chapter=ci, text=text, query=query, motion=_motion(len(scenes), rng),
                         transition=_pick(trans_mix, rng))
                first_scene_of_beat = sc
            g_end = min(e, g_start + g_max)
            if e - g_end < 2.0:
                g_end = e
            sc = add("graphic", g_start, g_end, chapter=ci, text=text, query=query, graphic=g,
                     transition="push" if not vertical else "zoom", motion=_motion(len(scenes), rng))
            sc.beat_times = _graphic_beats(g, bw, g_start, g_end, anchor_t)
            first_scene_of_beat = first_scene_of_beat or sc
            sfx.append(SfxCue(t=g_start, kind="whoosh", gain_db=-3))
            for bt in sc.beat_times:
                sfx.append(SfxCue(t=bt, kind="pop" if g.type in ("big_number", "quote", "compare") else "tick", gain_db=-6))
            cursor = g_end
        if e - cursor > 0.5:
            span = e - cursor
            n = max(1, round(span / ((shot_lo + shot_hi) / 2)))
            if span / n > shot_hi:
                n += 1
            cuts = [cursor]
            sent_starts = sorted({w.start - CUT_LEAD for w in bw if w.start > cursor + shot_lo * 0.8} |
                                 {w.start - CUT_LEAD for i, w in enumerate(bw) if i and bw[i - 1].sentence != w.sentence})
            word_starts = [w.start - CUT_LEAD for w in bw]
            for j in range(1, n):
                ideal = cursor + span * j / n + rng.uniform(-0.4, 0.4)
                cands = [t for t in sent_starts if abs(t - ideal) < 1.2 and t - cuts[-1] >= shot_lo * 0.8 and e - t >= shot_lo * 0.8]
                if not cands:
                    cands = [t for t in word_starts if t - cuts[-1] >= shot_lo * 0.8 and e - t >= shot_lo * 0.8]
                if not cands:
                    continue
                cuts.append(min(cands, key=lambda t: abs(t - ideal)))
            cuts.append(e)
            for j in range(len(cuts) - 1):
                a, b = cuts[j], cuts[j + 1]
                seg_words = [w.text for w in bw if a <= w.start < b]
                seg_text = " ".join(seg_words)
                q = query
                if j % 2 == 1 and not beat.visual:  # no curated query: vary with the segment's keywords
                    kw = keywords(seg_text, 2)
                    if kw:
                        q = f"{' '.join(kw)} {topic_kw}"
                sc = add("footage", a, b, chapter=ci, text=seg_text or text, query=q,
                         motion=_motion(len(scenes), rng), transition=_pick(trans_mix, rng))
                first_scene_of_beat = first_scene_of_beat or sc
                lt_target = lt_target or sc
                if vertical:
                    sfx.append(SfxCue(t=a, kind="swish", gain_db=-9))
        elif scenes and cursor < e:
            scenes[-1].end = round(e, 3)
        carrier = lt_target or first_scene_of_beat
        if beat.lower_third and carrier is not None:
            carrier.lower_third = beat.lower_third
            carrier.lower_third_at = 0.5
        # context-aware sound design on the words that call for it
        if preset.get("context_sfx", True):
            for w in bw:
                key = re.sub(r"[^A-Za-z]", "", w.text)
                for pat, kind, gain in _CTX_SFX:
                    if pat.match(key) and (not sfx or all(abs(c.t - w.start) > 5 or c.kind != kind for c in sfx[-6:])):
                        sfx.append(SfxCue(t=max(0.0, w.start - (0.15 if kind == "impact" else 0.05)), kind=kind, gain_db=gain))
                        break

    _fix_continuity(scenes, timing.duration)

    # chapter cards (long-form): overlay the chapter's opening, starting in the silence before it
    chapters: list[ChapterMark] = []
    music: list[MusicSection] = []
    for ci, t0 in enumerate(timing.chapter_starts):
        ch = script.chapters[ci]
        mark_t = 0.0 if ci == 0 else max(0.0, t0 - 1.25)
        chapters.append(ChapterMark(title=ch.title, start=round(mark_t, 2)))
        nxt = timing.chapter_starts[ci + 1] - 1.25 if ci + 1 < len(timing.chapter_starts) else timing.duration
        music.append(MusicSection(start=round(mark_t, 2), end=round(nxt, 2), mood=ch.mood))
        if ci == 0 or vertical or not preset.get("chapter_cards", True):
            continue
        if ci == 1 and preset.get("title_sequence") and timing.chapter_starts[1] - 1.25 - 2.0 > 0:
            # title sequence in the silence after the cold open
            prev_end = max((w.end for w in words if w.chapter == 0), default=0.0)
            ts0, ts1 = prev_end + 0.35, mark_t
            if ts1 - ts0 > 1.8:
                _insert(scenes, Scene(index=0, kind="title", start=round(ts0, 3), end=round(ts1, 3), chapter=0,
                                      title=script.title, subtitle=settings.channel_name, transition="dip",
                                      query=topic_kw))
                sfx.append(SfxCue(t=max(0, ts0 - 1.2), kind="riser", gain_db=-6))
                sfx.append(SfxCue(t=ts0 + 0.1, kind="impact", gain_db=0))
        card_end = t0 + 2.4
        _insert(scenes, Scene(index=0, kind="chapter", start=round(mark_t, 3), end=round(card_end, 3), chapter=ci,
                              title=ch.title, subtitle=f"Chapter {ci}", transition=preset.get("chapter_transition", "dip")))
        sfx.append(SfxCue(t=mark_t + 0.05, kind="impact", gain_db=-2))
        sfx.append(SfxCue(t=max(0, mark_t - 1.4), kind="riser", gain_db=-8))

    # outro: last beat with a call to action
    last_text = beats[beat_ids[-1]][1].text.lower() if beat_ids else ""
    if any(k in last_text for k in ("subscribe", "next video", "watch next", "follow")):
        t0 = starts[beat_ids[-1]]
        _insert(scenes, Scene(index=0, kind="outro", start=round(t0, 3), end=round(timing.duration, 3),
                              chapter=len(script.chapters) - 1, title=settings.channel_name,
                              text=beats[beat_ids[-1]][1].text, query=beats[beat_ids[-1]][1].visual or topic_kw,
                              transition="crossfade"))
    for i, sc in enumerate(scenes):
        sc.index = i

    emphasis = {norm_key(x) for _, b in beats for x in b.emphasis}
    captions = _phrases(words, vertical, emphasis)
    banner = None
    if settings.subscribe_banner and preset.get("subscribe_banner", True) and not vertical and timing.duration > 60:
        target = timing.duration * 0.22
        cands = [sc.start for sc in scenes if sc.kind == "footage" and sc.start > 20]
        banner = min(cands, key=lambda t: abs(t - target)) + 1.0 if cands else None

    return Plan(width=settings.width, height=settings.height, fps=settings.fps, duration=timing.duration,
                theme=theme["name"],
                caption_style=settings.caption_style or preset.get("captions") or theme["captions"]["style_" + ("short" if vertical else "long")],
                scenes=scenes, captions=captions, sfx=sorted(sfx, key=lambda c: c.t), music=music,
                chapters=chapters, banner_at=banner, channel_name=settings.channel_name, title=script.title,
                extra={k: preset.get(k) for k in ("name", "letterbox", "camera_shake", "kenburns", "progress_bar",
                                                  "music_db", "ambience", "ambience_db", "sfx_db")})


def _graphic_beats(g: GraphicSpec, bw: list[Word], s: float, e: float, anchor_t: Optional[float]) -> list[float]:
    span = e - s
    if g.type == "big_number" or g.type == "quote":
        t = anchor_t if anchor_t is not None and s <= anchor_t < e else s + 0.35
        return [round(t, 3)]
    n = max(1, len(g.items))
    times = []
    after = s
    for i, it in enumerate(g.items):
        key = (it.label or "").split()[0] if it.label else ""
        t = _word_time([w for w in bw if w.start < e], key, after) if key else None
        if t is None or t < after:
            t = s + 0.45 + (span * 0.62) * i / max(1, n - 1 if n > 1 else 1)
        times.append(round(min(t, e - 0.6), 3))
        after = t
    return times


def _fix_continuity(scenes: list[Scene], duration: float) -> None:
    scenes.sort(key=lambda s: s.start)
    if scenes:
        scenes[0].start = 0.0
    for a, b in zip(scenes, scenes[1:]):
        a.end = b.start
    if scenes:
        scenes[-1].end = round(duration, 3)
    scenes[:] = [s for s in scenes if s.end - s.start > 0.2]


def _insert(scenes: list[Scene], new: Scene) -> None:
    """Overlay a scene on the timeline, trimming/splitting what it covers."""
    out: list[Scene] = []
    for sc in scenes:
        if sc.end <= new.start or sc.start >= new.end:
            out.append(sc)
            continue
        if sc.start < new.start:
            left = sc.model_copy(deep=True)
            left.end = new.start
            if left.end - left.start > 0.6:
                out.append(left)
        if sc.end > new.end and sc.end - new.end < 1.8 and sc.kind == "footage":
            new.end = sc.end  # absorb a sliver into the overlay scene
        elif sc.end > new.end:
            right = sc.model_copy(deep=True)
            right.start = new.end
            right.beat_times = [t for t in right.beat_times if t >= new.end]
            if right.end - right.start > 0.6:
                out.append(right)
    out.append(new)
    out.sort(key=lambda s: s.start)
    scenes[:] = out
    _fix_continuity(scenes, max(s.end for s in scenes))

