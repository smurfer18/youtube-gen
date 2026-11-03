"""Timeline compositor and parallel encoder.

For every frame: find the active scene (and its neighbour during a
transition), render both, blend with the scene's transition (crossfade, dip,
push, zoom, cut), apply the theme's film finish (vignette, grain), then
overlays (progress bar, lower thirds, subscribe banner) and word-synced
captions.

The timeline is split into frame ranges rendered by worker processes, each
piping raw RGB into its own x264 encoder; segments are concatenated losslessly
(stream copy) and muxed with the final audio mix.
"""

from __future__ import annotations

import bisect
import math
import multiprocessing as mp
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from PIL import Image

from ytgen.models import Plan
from ytgen.render import fx
from ytgen.render.captions import Captions
from ytgen.render.overlays import Overlays
from ytgen.render.scenes import MediaCache, Renderer, make
from ytgen.render.theme import Theme
from ytgen.util import log

L = log.get("render")

TRANSITION_DUR = {"crossfade": None, "dip": 0.7, "push": 0.5, "zoom": 0.24, "cut": 0.0, "slide": 0.5}


class Timeline:
    def __init__(self, plan: Plan, theme_data: dict, captions: bool = True):
        self.plan = plan
        self.th = Theme(theme_data, plan.width, plan.height)
        self.media = MediaCache(plan, self.th)
        self.captions = Captions(plan, self.th) if captions and plan.captions else None
        self.overlays = Overlays(plan, self.th)
        self.starts = [s.start for s in plan.scenes]
        self.live: dict[int, Renderer] = {}
        self.default_td = float(self.th.transition.get("duration", 0.4))
        self.dip_color = Image.new("RGB", (plan.width, plan.height), self.th.rgb("bg"))

    def _r(self, i: int) -> Renderer:
        r = self.live.get(i)
        if r is None:
            r = make(self.plan.scenes[i], self.plan, self.th, self.media)
            self.live[i] = r
            for k in [k for k in self.live if k < i - 1]:
                self.live.pop(k).close()
        return r

    def _scene_frame(self, i: int, t: float) -> Image.Image:
        sc = self.plan.scenes[i]
        img = self._r(i).frame(t - sc.start)
        return img if img.mode == "RGB" else img.convert("RGB")

    def _tdur(self, i: int) -> float:
        tr = self.plan.scenes[i].transition
        if tr == "crossfade" and self.th.transition.get("default") not in (None, "crossfade"):
            tr = self.th.transition["default"]
        d = TRANSITION_DUR.get(tr)
        return self.default_td if d is None else d

    def _transition_name(self, i: int) -> str:
        tr = self.plan.scenes[i].transition
        if tr == "crossfade" and self.th.transition.get("default") not in (None, "crossfade"):
            return self.th.transition["default"]
        return tr

    def frame(self, t: float, fi: int = 0, finish: bool = True) -> Image.Image:
        n = len(self.plan.scenes)
        i = max(0, min(n - 1, bisect.bisect_right(self.starts, t) - 1))
        img: Optional[Image.Image] = None
        # are we inside the transition window of the cut into i, or of the cut into i+1?
        for j in (i, i + 1):
            if 0 < j < n:
                c = self.plan.scenes[j].start
                d = self._tdur(j)
                if d > 0 and c - d / 2 <= t < c + d / 2:
                    p = (t - (c - d / 2)) / d
                    a = self._scene_frame(j - 1, t)
                    b = self._scene_frame(j, t)
                    img = self._blend(a, b, p, self._transition_name(j))
                    break
        if img is None:
            img = self._scene_frame(i, t)
        if finish:
            img = fx.post(img, self.th, fi)
            self.overlays.draw(img, t)
            if self.captions:
                self.captions.draw(img, t)
        return img

    def _blend(self, a: Image.Image, b: Image.Image, p: float, kind: str) -> Image.Image:
        W, H = a.size
        if kind == "dip":
            if p < 0.5:
                return Image.blend(a, self.dip_color, fx.ease_in_out(p * 2))
            return Image.blend(self.dip_color, b, fx.ease_in_out((p - 0.5) * 2))
        if kind in ("push", "slide"):
            e = fx.ease_in_out(p)
            off = int(W * e)
            out = Image.new("RGB", (W, H))
            out.paste(a, (-off, 0))
            out.paste(b, (W - off, 0))
            return out
        if kind == "zoom":
            e = fx.ease_out(p)
            s = 1.12 - 0.12 * e
            if s > 1.001:
                bw, bh = W / s, H / s
                b = b.resize((W, H), Image.BILINEAR, box=((W - bw) / 2, (H - bh) / 2, (W + bw) / 2, (H + bh) / 2))
            return Image.blend(a, b, min(1.0, e * 1.6))
        if kind == "cut":
            return b if p >= 0.5 else a
        return Image.blend(a, b, fx.ease_in_out(p))

    def close(self) -> None:
        for r in self.live.values():
            r.close()
        self.live.clear()


# ------------------------------------------------------------- encoding ----

_G: dict = {}


def _encode_cmd(plan: Plan, out: Path, crf: int, preset: str, threads: int) -> list[str]:
    return [
        "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{plan.width}x{plan.height}", "-r", str(plan.fps), "-i", "-",
        "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-threads", str(threads),
        "-g", str(plan.fps * 2), "-profile:v", "high",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-movflags", "+faststart", str(out),
    ]


def _render_chunk(args) -> tuple[int, float]:
    idx, f0, f1, out = args
    plan: Plan = _G["plan"]
    tl = Timeline(plan, _G["theme"])
    proc = subprocess.Popen(_encode_cmd(plan, Path(out), _G["crf"], _G["preset"], _G["threads"]), stdin=subprocess.PIPE)
    t0 = time.time()
    try:
        for fi in range(f0, f1):
            img = tl.frame(fi / plan.fps, fi)
            proc.stdin.write(img.tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
        tl.close()
    if proc.returncode != 0:
        raise RuntimeError(f"x264 failed on chunk {idx}")
    return idx, time.time() - t0


def render(plan: Plan, theme_data: dict, work_dir: Path, audio: Optional[Path], out_path: Path,
           workers: int = 4, crf: int = 18, preset: str = "fast") -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    seg_dir = work_dir / "segments"
    if seg_dir.exists():
        shutil.rmtree(seg_dir)
    seg_dir.mkdir()
    total = int(math.ceil(plan.duration * plan.fps))
    workers = max(1, workers)
    n_chunks = max(workers, min(workers * 3, total // (plan.fps * 6) or 1))
    bounds = [round(total * k / n_chunks) for k in range(n_chunks + 1)]
    jobs = [(k, bounds[k], bounds[k + 1], str(seg_dir / f"seg_{k:04d}.mp4")) for k in range(n_chunks) if bounds[k + 1] > bounds[k]]
    _G.update(plan=plan, theme=theme_data, crf=crf, preset=preset, threads=max(1, (os.cpu_count() or 2) // workers))
    L.info("rendering %d frames (%dx%d@%d) in %d chunks on %d workers", total, plan.width, plan.height, plan.fps, len(jobs), workers)
    t0 = time.time()
    done = 0
    if workers == 1:
        for j in jobs:
            _render_chunk(j)
            done += 1
            L.info("chunk %d/%d", done, len(jobs))
    else:
        ctx = mp.get_context("fork")
        with ctx.Pool(workers) as pool:
            for _idx, _secs in pool.imap_unordered(_render_chunk, jobs):
                done += 1
                el = time.time() - t0
                L.info("chunk %d/%d done (%.0f%%, %.1f fps)", done, len(jobs), 100 * done / len(jobs),
                       sum(j[2] - j[1] for j in jobs[:done]) / max(el, 1e-3))
    lst = seg_dir / "list.txt"
    lst.write_text("".join(f"file '{Path(j[3]).name}'\n" for j in jobs))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst)]
    if audio and Path(audio).exists():
        cmd += ["-i", str(audio), "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += ["-c:v", "copy", "-movflags", "+faststart", "-shortest", str(out_path)]
    subprocess.run(cmd, check=True)
    L.info("video written: %s (%.1fs render)", out_path, time.time() - t0)
    return out_path


def still(plan: Plan, theme_data: dict, t: float, captions: bool = True) -> Image.Image:
    tl = Timeline(plan, theme_data, captions=captions)
    try:
        return tl.frame(t, int(t * plan.fps))
    finally:
        tl.close()
