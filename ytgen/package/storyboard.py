"""Storyboard review page: every scene with a rendered preview frame, timing,
narration, shot provenance/license, alternatives, and review flags. Review it
before rendering; swap a shot by setting ``asset_id`` to one of the scene's
``alternatives`` in plan.json (or any asset id), then ``ytgen render``."""

from __future__ import annotations

import html
import multiprocessing as mp
from pathlib import Path

from ytgen.models import Plan, Script
from ytgen.package.seo import ts
from ytgen.render import compositor
from ytgen.util import log

L = log.get("storyboard")
_G: dict = {}


def _thumb(args):
    i, t, path = args
    img = compositor.still(_G["plan"], _G["theme"], t, captions=False)
    img.thumbnail((480, 480))
    img.save(path, quality=80)
    return i


def build(plan: Plan, script: Script, theme_data: dict, out_dir: Path, workers: int = 4) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = out_dir / "frames"
    frames.mkdir(exist_ok=True)
    jobs = []
    for sc in plan.scenes:
        t = sc.start + min(sc.duration * 0.6, max(0.8, sc.duration - 0.4))
        if sc.beat_times:
            t = max(t, min(sc.end - 0.1, sc.beat_times[-1] + 1.3))
        jobs.append((sc.index, t, str(frames / f"scene_{sc.index:04d}.jpg")))
    _G.update(plan=plan, theme=theme_data)
    L.info("rendering %d storyboard frames", len(jobs))
    if workers > 1:
        with mp.get_context("fork").Pool(workers) as pool:
            list(pool.imap_unordered(_thumb, jobs, chunksize=4))
    else:
        for j in jobs:
            _thumb(j)
    rows = []
    for sc in plan.scenes:
        a = plan.assets.get(sc.asset_id) if sc.asset_id else None
        src = ""
        if a:
            src = (f"<div class=src><b>{html.escape(a.provider)}</b> · {html.escape(a.title[:90])}<br>"
                   f"{html.escape(a.author[:80])} · <i>{html.escape(a.license)}</i> "
                   f"{'<a href=' + html.escape(a.page_url) + ' target=_blank>source</a>' if a.page_url else ''}</div>")
        alts = ""
        if sc.alternatives:
            items = []
            for aid in sc.alternatives:
                b = plan.assets.get(aid)
                if b:
                    items.append(f"<li><code>{html.escape(aid)}</code> {html.escape(b.title[:60])} "
                                 f"{'<a href=' + html.escape(b.page_url) + ' target=_blank>view</a>' if b.page_url else ''}</li>")
            alts = "<details><summary>alternatives</summary><ul>" + "".join(items) + "</ul></details>"
        what = sc.kind
        if sc.graphic:
            what += f" · {sc.graphic.type}"
        if sc.kind == "chapter":
            what += f" · {html.escape(sc.title)}"
        flag = f"<div class=flag>⚑ {html.escape(sc.review_reason or 'review')}</div>" if sc.needs_review else ""
        rows.append(f"""<div class="card{' review' if sc.needs_review else ''}">
<img src="frames/scene_{sc.index:04d}.jpg" loading=lazy>
<div class=meta><div class=t>#{sc.index} · {ts(sc.start)}–{ts(sc.end)} · {sc.duration:.1f}s</div>
<div class=k>{what}</div>{flag}
<p>{html.escape(sc.text[:260])}</p>
<div class=q>query: <code>{html.escape(sc.query)}</code></div>{src}{alts}</div></div>""")
    review = sum(1 for s in plan.scenes if s.needs_review)
    page = f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Storyboard</title><style>
:root{{--bg:#101114;--fg:#eceae6;--mut:#9a978f;--card:#191a1f;--acc:#e5383b}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 Inter,system-ui,sans-serif}}
header{{padding:24px 28px;border-bottom:1px solid #2a2b31}} h1{{margin:0 0 6px;font-size:22px}}
.sum{{color:var(--mut)}} .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:16px;padding:20px 28px}}
.card{{background:var(--card);border-radius:10px;overflow:hidden;border:1px solid #24252b}}
.card.review{{border-color:var(--acc)}} .card img{{width:100%;display:block;background:#000}}
.meta{{padding:12px 14px}} .t{{color:var(--mut);font-size:12px}} .k{{font-weight:600;margin:2px 0 6px;text-transform:capitalize}}
.flag{{color:var(--acc);font-weight:600;font-size:12px}} p{{margin:6px 0}} .q,.src{{font-size:12px;color:var(--mut);margin-top:6px}}
a{{color:#8fb4ff}} code{{font-size:11px}} details{{margin-top:6px;font-size:12px;color:var(--mut)}}
</style></head><body><header><h1>{html.escape(script.title)}</h1>
<div class=sum>{len(plan.scenes)} scenes · {ts(plan.duration)} · theme {html.escape(plan.theme)} · {review} flagged for review.
Edit <code>plan.json</code> (e.g. set a scene's <code>asset_id</code> to an alternative), then run <code>ytgen render &lt;project&gt;</code>.</div>
</header><div class=grid>{''.join(rows)}</div></body></html>"""
    out = out_dir / "index.html"
    out.write_text(page, encoding="utf-8")
    return out
