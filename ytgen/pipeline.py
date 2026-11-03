"""Resumable production pipeline.

    research -> script -> voice -> plan -> media -> storyboard -> audio -> render -> package

Every stage persists its output in the project directory; re-running skips
finished stages unless forced, so you can edit any artifact (script.json,
plan.json, ...) and continue from there.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ytgen.config import Settings
from ytgen.models import Plan, Script, Timing
from ytgen.render import theme as theme_mod
from ytgen.util import log
from ytgen.util.text import slugify

L = log.get("pipeline")
STAGES = ["research", "script", "voice", "plan", "media", "storyboard", "audio", "render", "package", "qc"]


@dataclass
class Project:
    dir: Path
    settings: Settings
    topic: str = ""
    theme: str = ""
    timings: dict = field(default_factory=dict)

    # paths
    @property
    def script_path(self) -> Path: return self.dir / "script.json"
    @property
    def timing_path(self) -> Path: return self.dir / "timing.json"
    @property
    def plan_path(self) -> Path: return self.dir / "plan.json"
    @property
    def out_dir(self) -> Path: return self.dir / "output"

    def save_meta(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "project.json").write_text(json.dumps({
            "topic": self.topic, "theme": self.theme, "settings": self.settings.model_dump(),
            "stage_seconds": self.timings,
        }, indent=2))

    @classmethod
    def open(cls, path: Path, **overrides) -> "Project":
        meta = json.loads((path / "project.json").read_text())
        s = meta["settings"]
        s.update({k: v for k, v in overrides.items() if v is not None})
        from ytgen.config import load_settings
        settings = load_settings(**s)
        return cls(dir=path, settings=settings, topic=meta.get("topic", ""), theme=meta.get("theme", ""))

    def load_script(self) -> Script:
        return Script.model_validate_json(self.script_path.read_text())

    def load_timing(self) -> Timing:
        return Timing.model_validate_json(self.timing_path.read_text())

    def load_plan(self) -> Plan:
        return Plan.model_validate_json(self.plan_path.read_text())

    def theme_data(self) -> dict:
        return theme_mod.load(self.theme)

    def preset(self) -> dict:
        from ytgen import presets
        return presets.load("shorts" if self.settings.vertical and self.settings.preset_name == "documentary"
                            else self.settings.preset_name)


def _write(path: Path, model) -> None:
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")


def create(topic: str, settings: Settings, root: Path, script_file: Optional[Path] = None) -> Project:
    name = slugify(topic or (script_file.stem if script_file else "video"))
    d = root / f"{time.strftime('%Y%m%d')}-{name}"
    proj = Project(dir=d, settings=settings, topic=topic)
    d.mkdir(parents=True, exist_ok=True)
    if script_file:
        from ytgen.writers.loader import load_script
        script = load_script(script_file)
        if not script.topic:
            script.topic = topic or script.title
        proj.topic = proj.topic or script.topic
        _write(proj.script_path, script)
        shutil.copy(script_file, d / f"source{script_file.suffix}")
    proj.save_meta()
    return proj


def run(proj: Project, stop_after: Optional[str] = None, force: tuple[str, ...] = (),
        chapters: Optional[set[int]] = None, export_chapters: bool = False) -> Project:
    s = proj.settings
    force_set = set(force)
    if "all" in force_set:
        force_set = set(STAGES)

    def stage(name: str) -> bool:
        return name in force_set

    t0 = time.time()

    def done(name: str) -> bool:
        proj.timings[name] = round(time.time() - t0, 1)
        proj.save_meta()
        return stop_after == name

    # ---------------------------------------------------- research + script
    if not proj.script_path.exists() or stage("script"):
        from ytgen.research import Research, research
        from ytgen.writers import get_writer
        rpath = proj.dir / "research.json"
        res = Research(topic=proj.topic)
        if s.research:
            res = research(proj.topic, s.language, 4 if s.target_minutes > 12 and not s.vertical else 2)
            rpath.write_text(json.dumps({"topic": res.topic, "articles": [
                {"title": a.title, "url": a.url, "summary": a.summary, "sections": [[x.heading, x.text] for x in a.sections]}
                for a in res.articles]}, indent=2))
        if done("research"):
            return proj
        writer = get_writer(s.writer, s)
        script = writer.write(proj.topic, res, s)
        _write(proj.script_path, script)
    script = proj.load_script()
    if not proj.theme or proj.theme == "auto":
        proj.theme = s.theme if s.theme != "auto" else theme_mod.pick_auto(f"{script.topic} {script.title}", s.vertical)
    L.info("script: %r, %d chapters, %d words, theme %s", script.title, len(script.chapters), script.word_count(), proj.theme)
    if done("script"):
        return proj

    # ---------------------------------------------------------------- voice
    if not proj.timing_path.exists() or stage("voice"):
        from ytgen.voice import narrate
        pre = proj.preset()
        tg = pre.get("title_gap", 0.0) if pre.get("title_sequence") and not s.vertical and len(script.chapters) > 1 else 0.0
        timing = narrate(script, s, proj.dir / "voice", title_gap=tg)
        _write(proj.timing_path, timing)
    timing = proj.load_timing()
    if done("voice"):
        return proj

    # ----------------------------------------------------------- plan+media
    tdata = proj.theme_data()
    if not proj.plan_path.exists() or stage("plan") or stage("media"):
        from ytgen import planner
        from ytgen.media.select import assign_media
        plan = planner.plan(script, timing, s, tdata, proj.preset())
        _write(proj.dir / "plan.draft.json", plan)  # plan.json only once shots are assigned
        if done("plan"):
            return proj
        plan = assign_media(plan, script, s, proj.dir, tdata)
        from ytgen.render.html import prerender
        plan = prerender(plan, tdata, proj.dir)  # HyperFrames / Playwright motion clips
        _write(proj.plan_path, plan)
    plan = proj.load_plan()
    if done("media"):
        return proj

    # ----------------------------------------------------------- storyboard
    sb = proj.dir / "storyboard" / "index.html"
    if not sb.exists() or stage("storyboard") or stop_after == "storyboard":
        from ytgen.package import storyboard
        storyboard.build(plan, script, tdata, proj.dir / "storyboard", s.workers)
        L.info("storyboard: %s", sb)
    if done("storyboard"):
        return proj

    # ---------------------------------------------------------------- audio
    mix_path = proj.dir / "audio" / "mix.wav"
    if not mix_path.exists() or stage("audio") or stage("plan") or stage("media"):
        from ytgen.audio.mix import mix
        mix(plan, Path(timing.voice_path), s, proj.dir / "audio", seed=sum(map(ord, script.title)) % 1000)
    if done("audio"):
        return proj

    # --------------------------------------------------------------- render
    stem = slugify(script.title, 80)
    video = proj.out_dir / f"{stem}.mp4"
    if chapters is not None:  # independent chapter render/preview, no assembly
        from ytgen.render import compositor
        compositor.render(plan, tdata, proj.dir / "render", mix_path, video, s.workers, s.crf, s.encoder_preset,
                          chapters=chapters, export_chapters=True)
        return proj
    if not video.exists() or stage("render") or stage("audio") or stage("plan") or stage("media"):
        from ytgen.render import compositor
        # chunk cache makes this incremental: only chunks whose inputs changed are drawn
        compositor.render(plan, tdata, proj.dir / "render", mix_path, video, s.workers, s.crf, s.encoder_preset,
                          export_chapters=export_chapters)
    if done("render"):
        return proj

    # -------------------------------------------------------------- package
    from ytgen.package import seo, thumbnails
    thumbnails.make_thumbnails(plan, script, tdata, proj.out_dir, s.thumbnails)
    seo.write_pack(script, plan, timing, proj.out_dir, stem, s.language)
    try:
        from ytgen.media import manifest
        manifest.write(plan, proj.out_dir)
    except ImportError:
        pass
    if done("package"):
        return proj
    from ytgen import qc
    report = qc.run(proj.dir, video)
    done("qc")
    L.info("done: %s (QC %s)", video, report["verdict"])
    return proj
