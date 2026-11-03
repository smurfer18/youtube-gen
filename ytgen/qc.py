"""Automated quality control for a produced video.

Plan checks (fast, before/after render): timeline continuity, scene durations,
missing or uncertain-license assets, repeated footage, unreviewed shots,
caption coverage, YouTube chapter validity.

Output checks (one ffmpeg pass over the final MP4): decode errors, duration vs
plan, audio stream and A/V length match, black frames, frozen frames, long
silences, integrated loudness and true peak, plus frame errors recorded by the
renderer. Produces qc_report.json and a pass/fail verdict.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Optional

from ytgen.models import Plan, Timing
from ytgen.util import log

L = log.get("qc")


def _issue(level: str, check: str, msg: str, t: Optional[float] = None) -> dict:
    d = {"level": level, "check": check, "message": msg}
    if t is not None:
        d["time"] = round(t, 2)
    return d


def check_plan(plan: Plan, timing: Optional[Timing] = None) -> list[dict]:
    out: list[dict] = []
    sc = plan.scenes
    if not sc:
        return [_issue("error", "timeline", "plan has no scenes")]
    if abs(sc[0].start) > 1e-3:
        out.append(_issue("error", "timeline", "first scene does not start at 0", sc[0].start))
    if abs(sc[-1].end - plan.duration) > 0.05:
        out.append(_issue("error", "timeline", f"last scene ends at {sc[-1].end:.2f}s, video is {plan.duration:.2f}s"))
    for a, b in zip(sc, sc[1:]):
        if abs(a.end - b.start) > 0.01:
            out.append(_issue("error", "timeline", f"gap/overlap between scenes {a.index} and {b.index}", a.end))
    for s in sc:
        if s.duration < 0.6:
            out.append(_issue("warning", "pacing", f"scene {s.index} is only {s.duration:.2f}s", s.start))
        if s.kind == "footage" and s.duration > 14:
            out.append(_issue("warning", "pacing", f"footage scene {s.index} holds {s.duration:.1f}s", s.start))
        if s.kind in ("footage", "chapter", "outro") and s.asset_id:
            a = plan.assets.get(s.asset_id)
            if a is None or not a.path or not Path(a.path).exists():
                out.append(_issue("error", "assets", f"scene {s.index} asset {s.asset_id} missing on disk", s.start))
            elif getattr(a, "license_status", "") in ("uncertain", "rejected"):
                out.append(_issue("error", "license", f"scene {s.index} uses {a.license_status} license: {a.license}", s.start))
        if s.kind == "footage" and not s.asset_id:
            out.append(_issue("error", "assets", f"footage scene {s.index} has no asset", s.start))
        if s.needs_review:
            out.append(_issue("info", "review", f"scene {s.index}: {s.review_reason or 'flagged'}", s.start))
    use = Counter(s.asset_id for s in sc if s.kind == "footage" and s.asset_id)
    for aid, n in use.items():
        if n > 2:
            out.append(_issue("warning", "repetition", f"asset {aid} used in {n} footage scenes"))
    # visual monotony: same scene kind many times in a row
    run = 1
    for a, b in zip(sc, sc[1:]):
        run = run + 1 if (a.kind == b.kind == "footage" and a.motion.frame == b.motion.frame == "card") else 1
        if run == 4:
            out.append(_issue("info", "variety", "4+ photo-card scenes in a row", b.start))
    if timing is not None and plan.captions:
        cap_words = sum(len(p.words) for p in plan.captions)
        if cap_words < len(timing.words) * 0.98:
            out.append(_issue("error", "captions", f"captions cover {cap_words}/{len(timing.words)} words"))
    if plan.duration > 60 and len(plan.chapters) < 3:
        out.append(_issue("info", "chapters", "fewer than 3 chapters: YouTube chapters disabled"))
    return out


def check_video(video: Path, plan: Plan, render_dir: Optional[Path] = None, loudness_target: float = -14.0) -> tuple[list[dict], dict]:
    out: list[dict] = []
    stats: dict = {}
    pr = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate,duration:format=duration",
                         "-of", "json", str(video)], capture_output=True, text=True)
    if pr.returncode != 0:
        return [_issue("error", "container", f"ffprobe failed: {pr.stderr[-200:]}")], stats
    info = json.loads(pr.stdout)
    vs = [s for s in info["streams"] if s["codec_type"] == "video"]
    as_ = [s for s in info["streams"] if s["codec_type"] == "audio"]
    dur = float(info["format"]["duration"])
    stats["duration"] = round(dur, 2)
    if not vs:
        out.append(_issue("error", "container", "no video stream"))
    else:
        v = vs[0]
        stats["resolution"] = f"{v['width']}x{v['height']}"
        if (v["width"], v["height"]) != (plan.width, plan.height):
            out.append(_issue("error", "container", f"resolution {stats['resolution']} != plan {plan.width}x{plan.height}"))
    if not as_:
        out.append(_issue("error", "audio", "no audio stream"))
    else:
        vd, ad = float(vs[0].get("duration", dur)), float(as_[0].get("duration", dur))
        stats["av_drift"] = round(abs(vd - ad), 3)
        if abs(vd - ad) > 0.25:
            out.append(_issue("error", "sync", f"audio {ad:.2f}s vs video {vd:.2f}s"))
    if abs(dur - plan.duration) > 0.5:
        out.append(_issue("error", "duration", f"file is {dur:.2f}s, plan is {plan.duration:.2f}s"))

    # one decode pass: black + frozen frames, silence, loudness
    vf = "blackdetect=d=0.6:pix_th=0.06,freezedetect=n=0.0015:d=6"
    af = "silencedetect=n=-45dB:d=2.5,ebur128=peak=true"
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-v", "info", "-i", str(video), "-vf", vf, "-af", af,
                        "-f", "null", "-"], capture_output=True, text=True)
    log_ = r.stderr
    errs = [l for l in log_.splitlines() if re.search(r"(error|corrupt|invalid)", l, re.I) and "Error while filtering" not in l
            and "silencedetect" not in l]
    if r.returncode != 0 or errs:
        out.append(_issue("error", "decode", f"{len(errs)} decode error lines" + (f": {errs[0][:120]}" if errs else "")))
    for m in re.finditer(r"black_start:([\d.]+) black_end:([\d.]+)", log_):
        a, b = float(m.group(1)), float(m.group(2))
        out.append(_issue("error" if b - a > 2 else "warning", "black", f"black frames {a:.1f}-{b:.1f}s", a))
    for m in re.finditer(r"freeze_start: ([\d.]+)", log_):
        out.append(_issue("warning", "frozen", "picture static for 6+ s", float(m.group(1))))
    sil = [(float(a), float(b)) for a, b in re.findall(r"silence_start: ([\d.]+).*?silence_end: ([\d.]+)", log_, re.S)]
    for a, b in sil:
        if a > 1 and b < dur - 4:
            out.append(_issue("warning", "silence", f"{b - a:.1f}s of silence", a))
    m = re.search(r"Integrated loudness:\s+I:\s+(-?[\d.]+) LUFS.*?True peak:\s+Peak:\s+(-?[\d.]+) dBFS", log_, re.S)
    if m:
        I, tp = float(m.group(1)), float(m.group(2))
        stats["loudness_lufs"], stats["true_peak_db"] = I, tp
        if abs(I - loudness_target) > 1.5:
            out.append(_issue("warning", "loudness", f"integrated {I} LUFS (target {loudness_target})"))
        if tp > -0.5:
            out.append(_issue("warning", "loudness", f"true peak {tp} dBFS"))
    if render_dir and (render_dir / "chunks.json").exists():
        cj = json.loads((render_dir / "chunks.json").read_text())
        n = sum(c.get("n_errors", 0) for c in cj.get("chunks", []))
        if n:
            first = next(e for c in cj["chunks"] for e in c.get("errors", []))
            out.append(_issue("error", "render", f"{n} frames failed to render (e.g. {first})"))
    return out, stats


def run(project_dir: Path, video: Optional[Path] = None) -> dict:
    plan = Plan.model_validate_json((project_dir / "plan.json").read_text())
    timing = Timing.model_validate_json((project_dir / "timing.json").read_text()) if (project_dir / "timing.json").exists() else None
    issues = check_plan(plan, timing)
    stats: dict = {}
    if video is None:
        vids = [p for p in (project_dir / "output").glob("*.mp4")] if (project_dir / "output").exists() else []
        video = vids[0] if vids else None
    if video and video.exists():
        vi, stats = check_video(video, plan, project_dir / "render")
        issues += vi
    else:
        issues.append(_issue("error", "output", "no rendered video found"))
    verdict = "fail" if any(i["level"] == "error" for i in issues) else "pass"
    report = {"verdict": verdict, "stats": stats,
              "counts": dict(Counter(i["level"] for i in issues)), "issues": issues}
    (project_dir / "qc_report.json").write_text(json.dumps(report, indent=2))
    L.info("QC %s: %s %s", verdict.upper(), report["counts"], stats)
    return report
