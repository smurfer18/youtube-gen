"""YouTube pack: title, description (hook, chapters, sources, media credits),
tags, subtitles (SRT/VTT) and API-ready metadata."""

from __future__ import annotations

import json
from pathlib import Path

from ytgen.models import Plan, Script, Timing


def ts(t: float, hours: bool = False) -> str:
    t = max(0, int(t))
    h, m, s = t // 3600, (t % 3600) // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h or hours else f"{m}:{s:02d}"


def chapters(plan: Plan) -> list[tuple[float, str]]:
    """YouTube rules: first at 0:00, at least 3, each at least 10 s long."""
    marks = [(0.0 if i == 0 else c.start, c.title) for i, c in enumerate(plan.chapters)]
    out: list[tuple[float, str]] = []
    for t, title in marks:
        if out and t - out[-1][0] < 10:
            continue
        out.append((t, title))
    if out and plan.duration - out[-1][0] < 10 and len(out) > 1:
        out.pop()
    return out if len(out) >= 3 else []


def credits(plan: Plan) -> list[str]:
    used = []
    seen = set()
    for sc in plan.scenes:
        a = plan.assets.get(sc.asset_id) if sc.asset_id else None
        if a and a.id not in seen:
            seen.add(a.id)
            used.append(a)
    lines = []
    for a in used:
        who = a.author if a.author and a.author != "Unknown" else a.provider
        lines.append(f"• {a.title[:80]} — {who} ({a.license}) {a.page_url}".rstrip())
    return lines


def description(script: Script, plan: Plan) -> str:
    parts = [script.description.strip() or script.title]
    ch = chapters(plan)
    if ch:
        parts.append("Chapters\n" + "\n".join(f"{ts(t)} {title}" for t, title in ch))
    if script.sources:
        parts.append("Sources\n" + "\n".join(f"• {s.title} {s.url}".rstrip() for s in script.sources))
    cr = credits(plan)
    if cr:
        parts.append("Footage & images\n" + "\n".join(cr))
    parts.append("Music & sound design: original score generated for this video.")
    tags = [t for t in script.tags if " " not in t.strip()][:3] or [t.replace(" ", "") for t in script.tags[:3]]
    if tags:
        parts.append(" ".join(f"#{t.replace(' ', '')}" for t in tags))
    text = "\n\n".join(parts)
    return text[:4900]


def tags(script: Script) -> list[str]:
    out, total = [], 0
    for t in dict.fromkeys(x.strip().lower() for x in script.tags if x.strip()):
        if total + len(t) + 2 > 480:
            break
        out.append(t)
        total += len(t) + 2
    return out


def subtitle_cues(timing: Timing, max_chars: int = 42, max_dur: float = 6.0) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    cur, start, end, sent = [], 0.0, 0.0, -1
    for w in timing.words:
        text = " ".join(cur + [w.text])
        if cur and (len(text) > max_chars * 2 or w.start - start > max_dur or w.sentence != sent and len(" ".join(cur)) > 20):
            cues.append((start, end, _two_lines(" ".join(cur), max_chars)))
            cur = []
        if not cur:
            start = w.start
        cur.append(w.text)
        end = w.end
        sent = w.sentence
    if cur:
        cues.append((start, end, _two_lines(" ".join(cur), max_chars)))
    return cues


def _two_lines(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    words = s.split()
    best = min(range(1, len(words)), key=lambda k: abs(len(" ".join(words[:k])) - len(" ".join(words[k:]))))
    return " ".join(words[:best]) + "\n" + " ".join(words[best:])


def _srt_t(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_subtitles(timing: Timing, out_dir: Path, stem: str) -> tuple[Path, Path]:
    cues = subtitle_cues(timing)
    srt = out_dir / f"{stem}.srt"
    vtt = out_dir / f"{stem}.vtt"
    srt.write_text("".join(f"{i}\n{_srt_t(a)} --> {_srt_t(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(cues, 1)), encoding="utf-8")
    vtt.write_text("WEBVTT\n\n" + "".join(f"{_srt_t(a).replace(',', '.')} --> {_srt_t(b).replace(',', '.')}\n{t}\n\n" for a, b, t in cues), encoding="utf-8")
    return srt, vtt


def write_pack(script: Script, plan: Plan, timing: Timing, out_dir: Path, stem: str, language: str = "en") -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    desc = description(script, plan)
    meta = {
        "snippet": {
            "title": script.title[:100],
            "description": desc,
            "tags": tags(script),
            "categoryId": "27",  # Education
            "defaultLanguage": language,
            "defaultAudioLanguage": language,
        },
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False},
        "title_options": script.title_options,
        "chapters": [{"time": ts(t), "title": title} for t, title in chapters(plan)],
        "duration_seconds": round(plan.duration, 2),
        "review": [{"scene": sc.index, "time": ts(sc.start), "reason": sc.review_reason} for sc in plan.scenes if sc.needs_review],
        "notes": script.notes,
    }
    (out_dir / "metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "description.txt").write_text(desc, encoding="utf-8")
    (out_dir / "title.txt").write_text("\n".join([script.title, *script.title_options]), encoding="utf-8")
    write_subtitles(timing, out_dir, stem)
    return meta
