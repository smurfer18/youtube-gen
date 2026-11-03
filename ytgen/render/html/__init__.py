"""HTML motion-graphics scenes, pre-rendered to clips before compositing.

Two engines:
* HyperFrames (HeyGen, Apache-2.0) renders GSAP/DOM compositions such as the
  cinematic title sequence (`title.html`).
* A deterministic Playwright frame-stepper (`stepper.cjs`) renders procedural
  canvas/D3 pages that expose ``window.renderAt(t)``, such as the 3-D globe that
  flies to each new location (`globe.html`, D3 + world-atlas, Natural Earth
  data).

Clips are cached by a content hash. Any failure degrades gracefully: a map
becomes a footage scene, a title falls back to the built-in Pillow title card.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from ytgen.models import Plan, Scene
from ytgen.render import fonts as fonts_mod
from ytgen.render.theme import Theme
from ytgen.util import log, net

L = log.get("motion")
HERE = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("YTGEN_CACHE", Path.home() / ".cache" / "ytgen"))
HF_VERSION = "0.9.6"
VENDOR = {
    "d3.js": "https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js",
    "topojson.js": "https://cdn.jsdelivr.net/npm/topojson-client@3.1.0/dist/topojson-client.min.js",
    "countries-110m.json": "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-110m.json",
}
CHROME = next(iter(sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux*/chrome"))), None) if Path("/opt/pw-browsers").exists() else None


def _vendor(name: str) -> str:
    dest = CACHE / "vendor" / name
    net.download(VENDOR[name], dest, timeout=60)
    return dest.read_text(encoding="utf-8")


def hyperframes_bin() -> Optional[Path]:
    """Install the pinned HyperFrames CLI once into the cache (npm)."""
    root = CACHE / "node"
    binp = root / "node_modules" / ".bin" / "hyperframes"
    if binp.exists():
        return binp
    if not shutil.which("npm"):
        return None
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "package.json").exists():
        (root / "package.json").write_text('{"name":"ytgen-node","private":true}')
    r = subprocess.run(["npm", "install", f"hyperframes@{HF_VERSION}", "--no-audit", "--no-fund"], cwd=root,
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        L.warning("hyperframes install failed: %s", r.stderr[-300:])
        return None
    return binp if binp.exists() else None


_GEO_LOCK_T = [0.0]


def geocode(place: str) -> Optional[tuple[float, float]]:
    """Place name -> (lat, lon) via OpenStreetMap Nominatim (cached, 1 req/s)."""
    cache = CACHE / "geocode.json"
    data = json.loads(cache.read_text()) if cache.exists() else {}
    key = place.strip().lower()
    if key in data:
        return tuple(data[key]) if data[key] else None
    wait = _GEO_LOCK_T[0] + 1.1 - time.time()
    if wait > 0:
        time.sleep(wait)
    _GEO_LOCK_T[0] = time.time()
    try:
        res = net.get_json("https://nominatim.openstreetmap.org/search", params={"q": place, "format": "json", "limit": 1}, timeout=20)
        val = [float(res[0]["lat"]), float(res[0]["lon"])] if res else None
    except Exception as e:
        L.debug("geocode %s failed: %s", place, e)
        return None
    data[key] = val
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data))
    return tuple(val) if val else None


def _fill(tpl: str, values: dict) -> str:
    for k, v in values.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    return tpl


def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(c[:3])


def _mix(a, b, t: float):
    return tuple(int(a[i] * (1 - t) + b[i] * t) for i in range(3))


def render_globe(sc: Scene, plan: Plan, th: Theme, out: Path) -> bool:
    g = sc.graphic
    if g.lat is None or g.lon is None:
        ll = geocode(g.label)
        if not ll:
            return False
        g.lat, g.lon = ll
    W, H = plan.width, plan.height
    parts = [p.strip() for p in (g.title or g.label).replace("|", "·").split("·")]
    vals = {
        "W": W, "H": H, "dur": round(sc.duration, 3), "lon": g.lon, "lat": g.lat,
        "label": parts[0], "sub": " · ".join(parts[1:]),
        "bg": _hex(th.color("bg")), "ink": _hex(th.color("ink")), "muted": _hex(th.color("muted")),
        "accent": _hex(th.color("accent")), "ocean": _hex(_mix(th.color("bg"), th.color("surface"), 0.8)),
        "landc": _hex(_mix(th.color("surface"), th.color("ink"), 0.22)),
        "font": th.d["fonts"]["display"]["family"], "fs1": th.s(64), "fs2": th.s(34), "bar": th.s(6), "barw": th.s(200),
        "zoom": 1.25 if not th.vertical else 0.9, "cy": 0.47 if not th.vertical else 0.4, "cardTop": 74 if not th.vertical else 66,
        "D3": _vendor("d3.js"), "TOPO": _vendor("topojson.js"), "WORLD": _vendor("countries-110m.json"),
    }
    page = out.with_suffix(".html")
    page.write_text(_fill((HERE / "globe.html").read_text(), vals), encoding="utf-8")
    r = subprocess.run(["node", str(HERE / "stepper.cjs"), str(page), str(out), str(W), str(H), str(plan.fps), str(sc.duration)],
                       capture_output=True, text=True, timeout=900)
    page.unlink(missing_ok=True)
    if r.returncode != 0 or not out.exists():
        L.warning("globe render failed: %s", (r.stderr or r.stdout)[-300:])
        return False
    return True


def render_title(sc: Scene, plan: Plan, th: Theme, out: Path, bg_image: Optional[str]) -> bool:
    hf = hyperframes_bin()
    if hf is None:
        return False
    W, H = plan.width, plan.height
    work = out.parent / f"hf_{out.stem}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    for role, name in (("display", "display.ttf"), ("body", "body.ttf")):
        spec = th.d["fonts"][role]
        shutil.copy(fonts_mod.font_path(spec["family"], int(spec.get("weight", 700)), bool(spec.get("italic", False))), work / name)
    bgsrc = ""
    if bg_image and Path(bg_image).exists():
        shutil.copy(bg_image, work / "bg.jpg")
        bgsrc = "bg.jpg"
    title = sc.title.upper() if th.upper("display") else sc.title
    words = "".join(f'<span class="w">{w}</span>' for w in title.split())
    tfs = th.s(118 if len(title) < 28 else 92 if len(title) < 48 else 74)
    vals = {"W": W, "H": H, "dur": round(sc.duration, 3), "bg": _hex(th.color("bg")), "ink": _hex(th.color("ink")),
            "accent": _hex(th.color("accent")), "kicker": sc.subtitle or "", "words": words, "tfs": tfs,
            "kfs": th.s(28), "gap": th.s(22), "bar": th.s(6), "barw": th.s(240), "sweepw": th.s(420), "bgsrc": bgsrc,
            "upper": "text-transform:uppercase;" if th.upper("display") else ""}
    (work / "index.html").write_text(_fill((HERE / "title.html").read_text(), vals), encoding="utf-8")
    (work / "hyperframes.json").write_text('{"paths":{"assets":"assets"},"media":{"autoProxy":true}}')
    env = dict(os.environ, HYPERFRAMES_SKIP_SKILLS="1")
    if CHROME:
        env["PUPPETEER_EXECUTABLE_PATH"] = str(CHROME)
    r = subprocess.run([str(hf), "render", "-o", str(out), "-f", str(plan.fps), "-w", "2", "--quiet", "--crf", "15"],
                       cwd=work, capture_output=True, text=True, timeout=900, env=env)
    if r.returncode == 0 and out.exists():
        shutil.rmtree(work, ignore_errors=True)
    if r.returncode != 0 or not out.exists():
        L.warning("hyperframes title failed: %s", ((r.stderr or "") + (r.stdout or ""))[-1200:])
        return False
    return True


def _key(sc: Scene, plan: Plan, th: Theme, extra: str = "") -> str:
    blob = json.dumps({"s": sc.model_dump(exclude={"index", "start", "end", "clip_path"}), "d": round(sc.duration, 3),
                       "wh": [plan.width, plan.height, plan.fps], "theme": th.d, "x": extra}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def prerender(plan: Plan, theme_data: dict, project_dir: Path) -> Plan:
    th = Theme(theme_data, plan.width, plan.height)
    out_dir = (project_dir / "render" / "html").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    first_img = next((plan.assets[s.asset_id].path for s in plan.scenes
                      if s.asset_id in plan.assets and plan.assets[s.asset_id].kind == "image" and plan.assets[s.asset_id].path
                      and s.chapter <= 1 and s.kind == "footage"), None)
    n_ok = n_fail = 0
    for sc in plan.scenes:
        if sc.kind == "html" and sc.graphic and sc.graphic.type == "map":
            out = out_dir / f"map_{_key(sc, plan, th)}.mp4"
            ok = out.exists() or render_globe(sc, plan, th, out)
            if ok:
                sc.clip_path, n_ok = str(out), n_ok + 1
            else:  # no location / no browser: plain footage instead
                sc.kind, sc.graphic, n_fail = "footage", None, n_fail + 1
        elif sc.kind == "title":
            bg = sc.asset_id and plan.assets.get(sc.asset_id) and plan.assets[sc.asset_id].path or first_img
            out = out_dir / f"title_{_key(sc, plan, th, str(bg))}.mp4"
            ok = out.exists() or render_title(sc, plan, th, out, bg)
            if ok:
                sc.clip_path, n_ok = str(out), n_ok + 1
            else:
                n_fail += 1  # the Pillow Title renderer draws it instead
    if n_ok or n_fail:
        L.info("motion clips: %d rendered/cached, %d fell back", n_ok, n_fail)
    return plan
