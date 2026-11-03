"""Offline unit tests (no network): text, script loading, planning, captions,
packaging, audio synthesis and frame rendering."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ytgen.config import load_settings
from ytgen.models import (Asset, Beat, Chapter, GraphicItem, GraphicSpec, Plan, Scene, Script, Sentence, Timing,
                          Word)
from ytgen.util.text import format_figure, keywords, norm_key, parse_figure, split_sentences

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------- text utils --

def test_split_sentences_handles_abbreviations_and_decimals():
    s = split_sentences("Dr. Smith flew 3.5 miles. It was 1970! Was it fun? Yes.")
    assert s == ["Dr. Smith flew 3.5 miles.", "It was 1970!", "Was it fun?", "Yes."]


def test_norm_key_and_figures():
    assert norm_key("Café, 1970!") == "cafe1970"
    assert parse_figure("$1,250.5 million") == (1250.5, "$", " million", 1, True)
    assert format_figure(205000) == "205,000"


def test_keywords_prefers_names():
    kw = keywords("Commander Jim Lovell flew with Fred Haise around the Moon.")
    assert any("Jim Lovell" in k for k in kw)


# ----------------------------------------------------------- script loading --

def test_markdown_script_with_directives():
    from ytgen.writers.loader import load_script

    s = load_script(ROOT / "examples" / "apollo13.md")
    assert s.title.startswith("Apollo 13")
    assert len(s.chapters) == 7
    types = [b.graphic.type for c in s.chapters for b in c.beats if b.graphic]
    assert {"quote", "compare", "list", "big_number", "timeline"} <= set(types)
    num = next(b.graphic for c in s.chapters for b in c.beats if b.graphic and b.graphic.type == "big_number")
    assert num.value == 248655 and num.anchor == "two"
    assert any(b.lower_third for c in s.chapters for b in c.beats)


def test_markdown_inline_directive():
    from ytgen.writers.loader import parse_markdown

    s = parse_markdown("# T\n\n## One {mood=wonder}\n\nThe sky is big. [[visual: night sky stars]]\n")
    b = s.chapters[0].beats[0]
    assert b.visual == "night sky stars" and b.text == "The sky is big."
    assert s.chapters[0].mood == "wonder"


# ------------------------------------------------------------------ planning --

def _toy(vertical=False):
    texts = [
        "Fifty-six hours into the flight, a tank exploded. Warning lights flooded the panel.",
        "The crew was more than 200,000 miles from Earth, with oxygen running out fast.",
        "Engineers worked through the night. They built a filter from tape and plastic.",
        "Subscribe for more stories like this one.",
    ]
    script = Script(title="Toy", topic="Apollo 13", chapters=[
        Chapter(title="Bang", mood="tension", beats=[Beat(text=texts[0], visual="Apollo 13"), Beat(text=texts[1])]),
        Chapter(title="Fix", mood="driving", beats=[Beat(text=texts[2], lower_third="Houston · 1970"), Beat(text=texts[3])]),
    ])
    words, sents, t = [], [], 0.4
    sidx = 0
    starts = []
    for gi, (ci, txt) in enumerate([(0, texts[0]), (0, texts[1]), (1, texts[2]), (1, texts[3])]):
        if gi in (0, 2):
            starts.append(t)
        for sent in split_sentences(txt):
            s0 = t
            for w in sent.split():
                words.append(Word(text=w, start=round(t, 3), end=round(t + 0.3, 3), chapter=ci, beat=gi, sentence=sidx))
                t += 0.36
            sents.append(Sentence(text=sent, start=s0, end=t, chapter=ci, beat=gi))
            sidx += 1
        t += 0.4 if gi != 1 else 1.3
    timing = Timing(duration=round(t + 2, 3), words=words, sentences=sents, chapter_starts=starts, voice_path="x.wav")
    settings = load_settings(format="short" if vertical else "long", channel_name="Test")
    from ytgen.render import theme
    return script, timing, settings, theme.load("noir")


@pytest.mark.parametrize("vertical", [False, True])
def test_plan_is_continuous_and_detects_graphics(vertical):
    from ytgen import planner

    script, timing, settings, th = _toy(vertical)
    plan = planner.plan(script, timing, settings, th)
    assert plan.scenes[0].start == 0
    assert abs(plan.scenes[-1].end - timing.duration) < 1e-6
    for a, b in zip(plan.scenes, plan.scenes[1:]):
        assert abs(a.end - b.start) < 1e-6 and b.end > b.start
    kinds = [s.kind for s in plan.scenes]
    assert "graphic" in kinds  # 200,000 miles detected
    g = next(s for s in plan.scenes if s.kind == "graphic").graphic
    assert g.type == "big_number" and g.value == 200000
    assert "outro" in kinds
    if not vertical:
        assert "chapter" in kinds
    assert plan.captions and all(len(p.words) <= (3 if vertical else 7) for p in plan.captions)
    assert any(s.lower_third for s in plan.scenes)


def test_detect_timeline():
    from ytgen.planner import detect_graphic

    g = detect_graphic("In 1961 the goal was set, in 1969 they landed, and in 1970 it nearly ended.")
    assert g.type == "timeline" and [i.label for i in g.items] == ["1961", "1969", "1970"]


# ----------------------------------------------------------------- packaging --

def test_seo_chapters_and_subtitles(tmp_path):
    from ytgen import planner
    from ytgen.models import ChapterMark
    from ytgen.package import seo

    script, timing, settings, th = _toy()
    plan = planner.plan(script, timing, settings, th)
    plan.chapters = [ChapterMark(title="A", start=0), ChapterMark(title="B", start=12), ChapterMark(title="C", start=24)]
    plan.duration = 60
    assert [c[0] for c in seo.chapters(plan)] == [0, 12, 24]
    plan.chapters[1].start = 5  # too close to the first: merged
    assert len(seo.chapters(plan)) < 3 or seo.chapters(plan)[1][0] >= 10
    srt, vtt = seo.write_subtitles(timing, tmp_path, "t")
    assert srt.read_text().startswith("1\n00:00:00,")
    assert vtt.read_text().startswith("WEBVTT")


def test_license_filter():
    from ytgen.media import license_ok

    assert license_ok("CC BY-SA 4.0") and license_ok("Public domain") and license_ok("CC0")
    assert not license_ok("CC BY-NC 2.0") and not license_ok("CC BY-ND 3.0") and not license_ok("")


# --------------------------------------------------------------------- audio --

def test_music_and_sfx_render():
    from ytgen.audio import music, sfx
    from ytgen.models import MusicSection, SfxCue

    m = music.score([MusicSection(start=0, end=4, mood="tension"), MusicSection(start=4, end=8, mood="wonder")], 8, 1)
    assert m.shape == (2, 8 * music.SR) and np.isfinite(m).all() and 0.3 < np.abs(m).max() <= 0.71
    t = sfx.track([SfxCue(t=0.5, kind=k) for k in ("whoosh", "pop", "tick", "impact", "riser")], 4)
    assert t.shape[0] == 2 and np.abs(t).max() > 0.1


# ------------------------------------------------------------------- render --

@pytest.mark.parametrize("theme_name,vertical", [("noir", False), ("archive", False), ("pulse", True)])
def test_render_every_scene_kind(tmp_path, theme_name, vertical):
    from PIL import Image

    from ytgen.models import CaptionPhrase, CaptionWord
    from ytgen.render import compositor, theme

    W, H = (1080, 1920) if vertical else (1920, 1080)
    img = Image.linear_gradient("L").convert("RGB").resize((1600, 1000))
    p = tmp_path / "img.jpg"
    img.save(p)
    assets = {"a": Asset(id="a", path=str(p), width=1600, height=1000)}
    specs = [
        ("footage", None), ("chapter", None),
        ("graphic", GraphicSpec(type="big_number", value=1234, display="1,234", label="things")),
        ("graphic", GraphicSpec(type="bar_chart", items=[GraphicItem(label="a", value=1), GraphicItem(label="b", value=3)])),
        ("graphic", GraphicSpec(type="line_chart", items=[GraphicItem(label=str(i), value=v) for i, v in enumerate([1, 3, 2, 5])])),
        ("graphic", GraphicSpec(type="timeline", items=[GraphicItem(label="1900", display="x"), GraphicItem(label="1950", display="y")])),
        ("graphic", GraphicSpec(type="list", items=[GraphicItem(label="one"), GraphicItem(label="two")])),
        ("graphic", GraphicSpec(type="quote", title="Words matter.", label="Someone")),
        ("graphic", GraphicSpec(type="compare", items=[GraphicItem(label="A", display="10"), GraphicItem(label="B", display="20")])),
        ("kinetic", GraphicSpec(type="kinetic", items=[GraphicItem(label="cold"), GraphicItem(label="dark")])),
        ("outro", None),
    ]
    scenes = [Scene(index=i, kind=k, start=i * 2.0, end=i * 2.0 + 2, asset_id="a", graphic=g, title="T", subtitle="Chapter 1",
                    lower_third="Place · 1970" if i == 0 else None) for i, (k, g) in enumerate(specs)]
    caps = [CaptionPhrase(start=0.1, end=1.9, words=[CaptionWord(text="hello", start=0.1, end=0.6),
                                                     CaptionWord(text="world", start=0.7, end=1.2, emphasis=True)])]
    plan = Plan(width=W // 4, height=H // 4, fps=10, duration=len(specs) * 2.0, theme=theme_name,
                caption_style="pop" if vertical else "boxed", scenes=scenes, assets=assets, captions=caps)
    td = theme.load(theme_name)
    for sc in scenes:
        frame = compositor.still(plan, td, sc.start + 1.5)
        assert frame.size == (W // 4, H // 4)
        assert np.asarray(frame).std() > 1  # not a blank frame
    # a transition frame blends two scenes
    assert compositor.still(plan, td, 2.0).size == (W // 4, H // 4)
