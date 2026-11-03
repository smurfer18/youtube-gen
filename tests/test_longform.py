"""Tests for the long-form studio features (offline)."""

from __future__ import annotations

import json


import pytest
from PIL import Image

from ytgen.config import load_settings
from ytgen.models import ChapterMark, Plan, Scene
from tests.test_core import _toy


def test_presets_load_and_drive_planner():
    from ytgen import planner, presets

    assert {"documentary", "storytelling", "educational", "cinematic", "shorts"} <= set(presets.available())
    script, timing, settings, th = _toy()
    script.chapters[1].beats[0].lower_third = "Houston, Texas · April 1970"
    timing.chapter_starts[1] += 4.0  # room for a title sequence
    for w in timing.words:
        if w.chapter == 1:
            w.start += 4.0
            w.end += 4.0
    timing.duration += 4.0
    plan = planner.plan(script, timing, settings, th, presets.load("cinematic"))
    kinds = [s.kind for s in plan.scenes]
    assert "title" in kinds and "chapter" in kinds
    assert plan.extra["letterbox"] == 2.39 and plan.caption_style == "minimal"
    assert any(c.kind == "impact" for c in plan.sfx)            # 'exploded' -> context SFX
    assert set(s.transition for s in plan.scenes) - {"crossfade"}  # transition variety


def test_chunks_align_to_chapters_and_cache_keys():
    from ytgen.render.compositor import chunk_key, chunk_plan

    sc = [Scene(index=0, kind="footage", start=0, end=50), Scene(index=1, kind="footage", start=50, end=95)]
    plan = Plan(width=320, height=180, fps=10, duration=95, theme="noir", caption_style="boxed", scenes=sc,
                chapters=[ChapterMark(title="a", start=0), ChapterMark(title="b", start=50)])
    cp = chunk_plan(plan)
    assert cp[0][1] == 0 and cp[-1][2] == 950
    assert any(f0 == 500 for _, f0, _ in cp)                     # a chunk starts exactly at the chapter
    assert all((f1 - f0) <= 200 for _, f0, f1 in cp)              # <= 20 s at 10 fps
    k1 = chunk_key(plan, {"name": "noir"}, 0, 200, 17, "veryfast")
    k2 = chunk_key(plan, {"name": "noir"}, 700, 900, 17, "veryfast")
    plan.scenes[1].query = "changed"
    assert chunk_key(plan, {"name": "noir"}, 0, 200, 17, "veryfast") == k1   # unaffected chunk stays cached
    assert chunk_key(plan, {"name": "noir"}, 700, 900, 17, "veryfast") != k2


def test_license_classification():
    from ytgen.media.license import classify

    assert classify("CC BY-SA 4.0")[0] == "share_alike"
    assert classify("Public domain (NASA)")[0] == "public_domain"
    assert classify("CC0")[0] == "cc0"
    assert classify("CC BY-NC 2.0")[0] == "rejected"
    assert classify("Some custom terms")[0] == "uncertain"
    assert classify("Pexels License")[0] == "platform"


def test_dhash_near_duplicates(tmp_path):
    from ytgen.media.library import dhash, hamming

    im = Image.radial_gradient("L").convert("RGB").resize((800, 600))
    im.save(tmp_path / "a.jpg")
    im.resize((640, 480)).save(tmp_path / "b.jpg", quality=60)
    Image.linear_gradient("L").convert("RGB").resize((800, 600)).save(tmp_path / "c.jpg")
    a, b, c = (dhash(str(tmp_path / f)) for f in ("a.jpg", "b.jpg", "c.jpg"))
    assert hamming(a, b) <= 6 < hamming(a, c)


def test_cost_ledger_budget(tmp_path):
    from ytgen.media.generate import Ledger

    led = Ledger(tmp_path, budget=0.01)
    assert led.allow(0.0) and led.allow(0.003)
    led.add("replicate", "x", 0.003)
    led.add("replicate", "y", 0.003)
    led.add("replicate", "z", 0.003)
    assert not led.allow(0.003)
    assert json.loads((tmp_path / "costs.json").read_text())["total_usd"] == pytest.approx(0.009)
    assert not Ledger(tmp_path, budget=0.0).allow(0.17)


def test_qc_plan_checks():
    from ytgen.qc import check_plan

    sc = [Scene(index=0, kind="footage", start=0, end=5), Scene(index=1, kind="footage", start=5.5, end=9)]
    plan = Plan(width=320, height=180, fps=10, duration=10, theme="noir", caption_style="boxed", scenes=sc)
    checks = {i["check"] for i in check_plan(plan)}
    assert {"timeline", "assets"} <= checks


def test_longform_writer_flow(monkeypatch):
    from ytgen.research import Article, Research
    from ytgen.writers import claude

    calls = []

    def fake_call(settings, system, user, schema, max_tokens=64000):
        calls.append(schema)
        if schema is claude.OUTLINE_SCHEMA:
            return {"title": "T", "title_options": ["a"], "thumbnail_text": "X", "description": "d", "tags": ["t"],
                    "sources": [{"title": "W", "url": "u"}],
                    "chapters": [{"title": f"C{i}", "mood": "tension", "purpose": "p", "key_facts": [f"f{i}"],
                                  "ends_on": "e", "words": 400} for i in range(6)]}
        if schema is claude.CHAPTER_SCHEMA:
            n = len([c for c in calls if c is claude.CHAPTER_SCHEMA])
            return {"summary": f"s{n}", "beats": [
                {"text": f"Unique beat {n} about the subject in chapter {n} with fresh details.", "visual": "v",
                 "lower_third": None, "emphasis": [], "graphic": None},
                {"text": "This exact sentence is repeated in every single chapter of the draft script.", "visual": "v",
                 "lower_third": None, "emphasis": [], "graphic": None}]}
        return {"fixes": []}

    monkeypatch.setattr(claude, "_call_json", fake_call)
    settings = load_settings(target_minutes=20, anthropic_api_key="x")
    res = Research(topic="t", articles=[Article(title="W", url="u", summary="s" * 300)])
    script = claude.ClaudeWriter().write("t", res, settings)
    assert len(script.chapters) == 6
    assert sum(1 for c in calls if c is claude.CHAPTER_SCHEMA) == 6
    repeated = sum(1 for c in script.chapters for b in c.beats if b.text.startswith("This exact"))
    assert repeated == 1                                          # dedupe kept only the first
    assert sum(1 for c in calls if c is claude.FIX_SCHEMA) == 6   # per-chapter fact check


def test_formats():
    s = load_settings(format="square")
    assert (s.width, s.height) == (1080, 1080)
    s = load_settings(format="long", resolution=2160)
    assert (s.width, s.height) == (3840, 2160)
    s = load_settings(format="short", resolution=720)
    assert (s.width, s.height) == (720, 1280)



