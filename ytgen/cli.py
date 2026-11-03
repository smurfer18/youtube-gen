"""Command line interface.

  ytgen make "The Apollo 13 rescue" --minutes 8
  ytgen make "Why octopuses are so smart" --format short
  ytgen make --script examples/apollo13.md --theme orbit
  ytgen make "topic" --stop-after storyboard    # review, edit plan.json, then:
  ytgen render projects/20261103-topic
  ytgen batch topics.txt --format short          # many videos
  ytgen upload projects/20261103-topic --publish-at 2026-11-10T15:00:00Z
  ytgen themes | ytgen voices --lang en | ytgen doctor
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from ytgen import __version__
from ytgen.util import log


def _settings_from(args) -> dict:
    o = {}
    for k in ("format", "theme", "voice", "writer", "language", "workers", "crf", "fps", "resolution", "ai_images"):
        v = getattr(args, k, None)
        if v is not None:
            o[k] = v
    if getattr(args, "preset", None):
        o["preset_name"] = args.preset
    if getattr(args, "rate", None):
        o["voice_rate"] = args.rate
    if getattr(args, "x264", None):
        o["encoder_preset"] = args.x264
    if getattr(args, "ai_budget", None) is not None:
        o["ai_budget_usd"] = args.ai_budget
    if getattr(args, "allow_uncertain", False):
        o["allow_uncertain_licenses"] = True
    if getattr(args, "preview", False):  # fast draft: 540p, 24 fps, ultrafast encode
        o.update(resolution=540, fps=24, encoder_preset="ultrafast", crf=26)
    if getattr(args, "minutes", None):
        o["target_minutes"] = args.minutes
    if getattr(args, "channel", None):
        o["channel_name"] = args.channel
    if getattr(args, "captions", None):
        o["caption_style"] = args.captions
    if getattr(args, "media", None):
        o["media_sources"] = [m.strip() for m in args.media.split(",") if m.strip()]
    if getattr(args, "no_music", False):
        o["music"] = False
    if getattr(args, "no_sfx", False):
        o["sfx"] = False
    if getattr(args, "no_research", False):
        o["research"] = False
    if getattr(args, "no_fact_check", False):
        o["fact_check"] = False
    return o


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--format", choices=["long", "short", "square"], help="16:9 long-form (default), 9:16 Short, 1:1 square")
    p.add_argument("--resolution", type=int, help="short side in px: 720, 1080 (default), 1440, 2160 (4K)")
    p.add_argument("--preview", action="store_true", help="fast draft render: 540p, 24 fps")
    p.add_argument("--minutes", type=float, help="target length for long-form (default 8; 15-60 supported)")
    p.add_argument("--preset", help="editing preset: documentary | storytelling | educational | cinematic | shorts")
    p.add_argument("--theme", help="visual theme or 'auto' (see `ytgen themes`)")
    p.add_argument("--voice", help="edge:<voice> | elevenlabs:<voice_id> | file:<narration.wav>")
    p.add_argument("--writer", help="auto | claude | extractive")
    p.add_argument("--rate", help="narration speed, e.g. -6%% (Edge voices)")
    p.add_argument("--captions", choices=["boxed", "karaoke", "pop", "minimal"], help="caption style override")
    p.add_argument("--media", help="comma list: wikimedia,nasa,archive,openverse,pexels,pixabay,unsplash,smithsonian,local:/dir")
    p.add_argument("--ai-images", choices=["off", "pollinations", "openai", "replicate"], help="AI fallback when no footage fits")
    p.add_argument("--ai-budget", type=float, help="max USD for paid AI generation in this project (default 0)")
    p.add_argument("--allow-uncertain", action="store_true", help="use assets whose license could not be verified")
    p.add_argument("--channel", help="channel name for banner/outro")
    p.add_argument("--language", help="script language (default en)")
    p.add_argument("--workers", type=int, help="render processes")
    p.add_argument("--crf", type=int, help="x264 quality (lower is better, default 17)")
    p.add_argument("--x264", help="x264 preset (default veryfast)")
    p.add_argument("--fps", type=int)
    p.add_argument("--no-music", action="store_true")
    p.add_argument("--no-sfx", action="store_true")
    p.add_argument("--no-research", action="store_true")
    p.add_argument("--no-fact-check", action="store_true")
    p.add_argument("--config", help="ytgen.yaml with defaults")


def cmd_make(args) -> int:
    from ytgen import pipeline
    from ytgen.config import load_settings
    if not args.topic and not args.script:
        print("give a topic or --script FILE", file=sys.stderr)
        return 2
    settings = load_settings(args.config, **_settings_from(args))
    script = Path(args.script) if args.script else None
    proj = pipeline.create(args.topic or "", settings, Path(args.out), script)
    if settings.theme != "auto":
        proj.theme = settings.theme
    pipeline.run(proj, stop_after=args.stop_after)
    _report(proj)
    return 0


def cmd_render(args) -> int:
    from ytgen import pipeline
    proj = pipeline.Project.open(Path(args.project), **_settings_from(args))
    if args.theme:
        proj.theme = args.theme
    force = tuple(args.force or ())
    if not force and not args.stop_after:
        force = ("storyboard", "render")  # re-render from the (possibly edited) plan; unchanged chunks are cached
    chapters = {int(x) for x in args.chapters.split(",")} if args.chapters else None
    pipeline.run(proj, stop_after=args.stop_after, force=force, chapters=chapters, export_chapters=args.export_chapters)
    _report(proj)
    return 0


def cmd_regen(args) -> int:
    """Swap or re-search one scene's shot, then re-render (only affected chunks redraw)."""
    from ytgen import pipeline
    from ytgen.media.select import Selector
    proj = pipeline.Project.open(Path(args.project))
    plan, script = proj.load_plan(), proj.load_script()
    sc = plan.scenes[args.scene]
    if args.asset:
        if args.asset not in plan.assets:
            print(f"unknown asset {args.asset}; alternatives: {sc.alternatives}", file=sys.stderr)
            return 2
        a = plan.assets[args.asset]
        if not a.path:
            sel = Selector(proj.settings, proj.dir, script)
            a = sel.fetch(a)
            if a is None:
                print("download failed", file=sys.stderr)
                return 1
            plan.assets[a.id] = a
        sc.asset_id = a.id
    else:
        sel = Selector(proj.settings, proj.dir, script)
        sel.theme = proj.theme_data()
        sel.used = {s.asset_id for s in plan.scenes if s.asset_id}
        pick, alts, review = sel.choose([args.query or sc.query])
        if pick is None:
            print("no shot found", file=sys.stderr)
            return 1
        plan.assets[pick.id] = pick
        for b in alts:
            plan.assets.setdefault(b.id, b)
        sc.asset_id, sc.alternatives, sc.needs_review = pick.id, [b.id for b in alts], review
        if args.query:
            sc.query = args.query
    proj.plan_path.write_text(plan.model_dump_json(indent=2))
    print(f"scene {sc.index} -> {sc.asset_id}")
    if not args.no_render:
        pipeline.run(proj, force=("storyboard", "render"))
        _report(proj)
    return 0


def cmd_qc(args) -> int:
    from ytgen import qc
    r = qc.run(Path(args.project))
    for i in r["issues"]:
        if i["level"] != "info" or args.all:
            print(f"{i['level']:<8} {i['check']:<11} {i.get('time', ''):>8}  {i['message']}")
    print(f"\nQC {r['verdict'].upper()}  {r['counts']}  {r['stats']}")
    return 0 if r["verdict"] == "pass" else 1


def cmd_library(args) -> int:
    from ytgen.media import library
    if args.action == "stats":
        print(json.dumps(library.stats(), indent=2))
        return 0
    for a in library.search(" ".join(args.query), args.limit):
        print(f"{a.id:<34} {a.license_status:<13} {a.provider:<18} {a.title[:60]}\n{'':<34} {a.path}")
    return 0


def cmd_presets(args) -> int:
    from ytgen import presets
    for n in presets.available():
        print(f"{n:<13} {presets.load(n).get('description', '')}")
    return 0


def cmd_drive(args) -> int:
    from ytgen.package import gdrive
    ids = gdrive.upload_project(Path(args.project), args.folder, include_video=not args.no_video)
    print(json.dumps(ids, indent=2))
    return 0


def cmd_batch(args) -> int:
    topics = [l.strip() for l in Path(args.file).read_text().splitlines() if l.strip() and not l.startswith("#")]
    rc = 0
    for t in topics:
        ns = argparse.Namespace(**{**vars(args), "topic": t, "script": None})
        try:
            cmd_make(ns)
        except Exception as e:  # keep going
            log.get("batch").error("%s failed: %s", t, e)
            rc = 1
    return rc


def cmd_themes(args) -> int:
    from ytgen.render import theme
    for n in theme.available():
        d = theme.load(n)
        print(f"{n:<10} {d.get('use', '')}\n{'':<10} {d.get('description', '')}\n")
    return 0


def cmd_voices(args) -> int:
    import asyncio

    import edge_tts
    import edge_tts.voices as ev

    from ytgen.util import net
    ev._SSL_CTX = net.ssl_context() if hasattr(ev, "_SSL_CTX") else None
    vs = asyncio.run(edge_tts.list_voices())
    for v in vs:
        if args.lang and not v["Locale"].lower().startswith(args.lang.lower()):
            continue
        print(f"edge:{v['ShortName']:<40} {v['Gender']:<7} {', '.join(v.get('VoiceTag', {}).get('VoicePersonalities', []))}")
    return 0


def cmd_doctor(args) -> int:
    from ytgen.config import load_settings
    s = load_settings()
    ok = True

    def line(name, good, note=""):
        nonlocal ok
        ok = ok and (good or name.startswith("optional"))
        print(f"{'✓' if good else '✗'} {name:<34} {note}")

    line("ffmpeg", shutil.which("ffmpeg") is not None)
    if shutil.which("ffmpeg"):
        filt = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        line("ffmpeg loudnorm/sidechaincompress", "loudnorm" in filt and "sidechaincompress" in filt)
        enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
        line("ffmpeg libx264", "libx264" in enc)
    try:
        import edge_tts  # noqa: F401
        line("edge-tts", True)
    except ImportError:
        line("edge-tts", False, "pip install edge-tts")
    try:
        import anthropic  # noqa: F401
        has = True
    except ImportError:
        has = False
    line("optional: anthropic SDK", has, "pip install 'ytgen[claude]'")
    line("optional: ANTHROPIC_API_KEY", bool(s.anthropic_api_key), "Claude writer + fact checker")
    line("optional: PEXELS_API_KEY", bool(s.pexels_api_key), "stock photos + video b-roll")
    line("optional: PIXABAY_API_KEY", bool(s.pixabay_api_key), "stock photos")
    line("optional: ELEVENLABS_API_KEY", bool(s.elevenlabs_api_key), "premium voices")
    from ytgen.util import net
    for name, url in [("Wikipedia", "https://en.wikipedia.org/api/rest_v1/page/summary/Moon"),
                      ("Wikimedia Commons", "https://commons.wikimedia.org/w/api.php?action=query&format=json"),
                      ("NASA images", "https://images-api.nasa.gov/search?q=moon&page_size=1")]:
        try:
            net.session().get(url, timeout=10).raise_for_status()
            line(f"network: {name}", True)
        except Exception as e:
            line(f"network: {name}", False, str(e)[:60])
    return 0 if ok else 1


def cmd_upload(args) -> int:
    from ytgen.package import youtube
    d = Path(args.project) / "output"
    meta = json.loads((d / "metadata.json").read_text())
    video = next(d.glob("*.mp4"))
    thumb = d / f"thumbnail_{args.thumbnail}.jpg"
    srt = next(d.glob("*.srt"), None)
    vid = youtube.upload(video, meta, thumb, srt, args.privacy, args.publish_at)
    print(f"https://youtu.be/{vid}")
    return 0


def cmd_auth(args) -> int:
    from ytgen.package import youtube
    tok = youtube.authorize(Path(args.client_secrets))
    print("\nYOUTUBE_REFRESH_TOKEN=" + tok)
    return 0


def cmd_preview(args) -> int:
    from ytgen import pipeline
    from ytgen.render import compositor
    proj = pipeline.Project.open(Path(args.project))
    img = compositor.still(proj.load_plan(), proj.theme_data(), args.at)
    out = Path(args.out or proj.dir / f"preview_{args.at:.1f}.jpg")
    img.save(out, quality=90)
    print(out)
    return 0


def _report(proj) -> None:
    out = proj.out_dir
    print()
    print(f"project:    {proj.dir}")
    sb = proj.dir / "storyboard" / "index.html"
    if sb.exists():
        print(f"storyboard: {sb}")
    for p in sorted(out.glob("*")) if out.exists() else []:
        print(f"output:     {p}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ytgen", description="Automatic faceless YouTube video generator")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("make", help="produce a video from a topic or a script")
    p.add_argument("topic", nargs="?")
    p.add_argument("--script", help="your own script (.md with directives, .json or .yaml)")
    p.add_argument("--out", default="projects")
    p.add_argument("--stop-after", choices=["research", "script", "voice", "plan", "media", "storyboard", "audio", "render", "package"])
    _add_common(p)
    p.set_defaults(fn=cmd_make)

    p = sub.add_parser("render", help="continue/re-render an existing project (after editing plan.json etc.)")
    p.add_argument("project")
    p.add_argument("--force", nargs="*", help="stages to redo: script voice plan media storyboard audio render all")
    p.add_argument("--stop-after", choices=["voice", "plan", "media", "storyboard", "audio", "render", "package"])
    p.add_argument("--chapters", help="render/export only these chapter indexes, e.g. 3,4")
    p.add_argument("--export-chapters", action="store_true", help="also write one MP4 per chapter")
    _add_common(p)
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("regen", help="regenerate one scene's shot (alternative asset or new query) and re-render")
    p.add_argument("project")
    p.add_argument("--scene", type=int, required=True)
    p.add_argument("--asset", help="asset id (e.g. one of the scene's alternatives)")
    p.add_argument("--query", help="new search query for this scene")
    p.add_argument("--no-render", action="store_true")
    p.set_defaults(fn=cmd_regen)

    p = sub.add_parser("qc", help="run automated quality control on a project")
    p.add_argument("project")
    p.add_argument("--all", action="store_true", help="include info-level notes")
    p.set_defaults(fn=cmd_qc)

    p = sub.add_parser("library", help="search the shared media library")
    p.add_argument("action", choices=["search", "stats"])
    p.add_argument("query", nargs="*")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_library)

    sub.add_parser("presets", help="list editing presets").set_defaults(fn=cmd_presets)

    p = sub.add_parser("drive", help="upload a project's deliverables to a Google Drive folder")
    p.add_argument("project")
    p.add_argument("--folder", required=True, help="Drive folder id (uploads stay inside it)")
    p.add_argument("--no-video", action="store_true")
    p.set_defaults(fn=cmd_drive)

    p = sub.add_parser("batch", help="one video per line of a text file")
    p.add_argument("file")
    p.add_argument("--out", default="projects")
    p.add_argument("--stop-after", default=None)
    _add_common(p)
    p.set_defaults(fn=cmd_batch)

    sub.add_parser("themes", help="list themes").set_defaults(fn=cmd_themes)
    p = sub.add_parser("voices", help="list Edge neural voices")
    p.add_argument("--lang", default="en")
    p.set_defaults(fn=cmd_voices)
    sub.add_parser("doctor", help="check dependencies, keys and network").set_defaults(fn=cmd_doctor)

    p = sub.add_parser("preview", help="render one frame of a project")
    p.add_argument("project")
    p.add_argument("--at", type=float, default=5.0)
    p.add_argument("--out")
    p.set_defaults(fn=cmd_preview)

    p = sub.add_parser("upload", help="publish to YouTube (Data API v3)")
    p.add_argument("project")
    p.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    p.add_argument("--publish-at", help="RFC3339 time to schedule publication")
    p.add_argument("--thumbnail", default="A", choices=["A", "B", "C"])
    p.set_defaults(fn=cmd_upload)

    p = sub.add_parser("youtube-auth", help="one-time OAuth to get a YouTube refresh token")
    p.add_argument("client_secrets")
    p.set_defaults(fn=cmd_auth)

    args = ap.parse_args(argv)
    log.setup(args.verbose)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
