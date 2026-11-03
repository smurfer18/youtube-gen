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
import hashlib
import json
import math
import multiprocessing as mp
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from ytgen.models import Plan
from ytgen.render import fx
from ytgen.render.captions import Captions
from ytgen.render.overlays import Overlays
from ytgen.render.scenes import MediaCache, Renderer, make
from ytgen.render.theme import Theme
from ytgen.util import log

L = log.get("render")

TRANSITION_DUR = {"crossfade": None, "dip": 0.7, "push": 0.5, "zoom": 0.24, "cut": 0.0, "slide": 0.5,
                  "whip": 0.34, "zoom_through": 0.5, "light_leak": 0.9}


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
        shake = float(self.plan.extra.get("camera_shake") or 0)
        if shake and self.plan.scenes[i].kind == "footage" and self._mood(t) in ("tension", "driving"):
            img = self._drift(img, t, shake)
        if finish:
            img = fx.post(img, self.th, fi)
            lb = float(self.plan.extra.get("letterbox") or 0)
            if lb and not self.th.vertical:
                bar = int(round((self.plan.height - self.plan.width / lb) / 2))
                if bar > 0:
                    d = ImageDraw.Draw(img)
                    d.rectangle((0, 0, self.plan.width, bar), fill=(0, 0, 0))
                    d.rectangle((0, self.plan.height - bar, self.plan.width, self.plan.height), fill=(0, 0, 0))
            self.overlays.draw(img, t)
            if self.captions:
                self.captions.draw(img, t)
        return img

    def _mood(self, t: float) -> str:
        for m in self.plan.music:
            if m.start <= t < m.end:
                return m.mood
        return ""

    def _drift(self, img: Image.Image, t: float, amount: float) -> Image.Image:
        """Handheld-camera drift: smooth, low-frequency, sub-pixel offsets and a hint of roll."""
        W, H = img.size
        k = amount * self.th.u
        dx = k * (3.0 * math.sin(t * 1.3) + 1.6 * math.sin(t * 2.9 + 1.1))
        dy = k * (2.2 * math.sin(t * 1.7 + 0.4) + 1.2 * math.sin(t * 3.7 + 2.3))
        z = 1.0 + 0.012 * amount
        bw, bh = W / z, H / z
        x0, y0 = (W - bw) / 2 + dx, (H - bh) / 2 + dy
        return img.resize((W, H), Image.BILINEAR, box=(x0, y0, x0 + bw, y0 + bh))

    def _leak(self, W: int, H: int) -> Image.Image:
        if getattr(self, "_leak_img", None) is None:
            import numpy as np
            y, x = np.ogrid[0:H, 0:W]
            g = np.exp(-(((x - W * 0.78) / (W * 0.45)) ** 2 + ((y - H * 0.3) / (H * 0.7)) ** 2))
            col = np.array([255, 170, 90], np.float32)
            self._leak_img = Image.fromarray((g[..., None] * col).clip(0, 255).astype("uint8"), "RGB")
        return self._leak_img

    def _blend(self, a: Image.Image, b: Image.Image, p: float, kind: str) -> Image.Image:
        W, H = a.size
        if kind == "whip":  # fast push with motion smear
            e = fx.ease_in_out(p)
            off = int(W * e)
            out = Image.new("RGB", (W, H))
            out.paste(a, (-off, 0))
            out.paste(b, (W - off, 0))
            blur = int(abs(math.sin(math.pi * p)) * W * 0.03)
            if blur > 1:
                out = out.filter(ImageFilter.BoxBlur(blur // 2)) if blur > 3 else out
            return out
        if kind == "zoom_through":  # push into the outgoing shot, emerge in the new one
            e = fx.ease_in_out(p)
            s = 1.0 + 0.35 * e
            aw, ah = W / s, H / s
            az = a.resize((W, H), Image.BILINEAR, box=((W - aw) / 2, (H - ah) / 2, (W + aw) / 2, (H + ah) / 2))
            s2 = 1.15 - 0.15 * e
            bw, bh = W / s2, H / s2
            bz = b.resize((W, H), Image.BILINEAR, box=((W - bw) / 2, (H - bh) / 2, (W + bw) / 2, (H + bh) / 2))
            return Image.blend(az, bz, fx.clamp((p - 0.25) / 0.5))
        if kind == "light_leak":  # warm flare blooms over the dissolve
            base = Image.blend(a, b, fx.ease_in_out(p))
            amt = math.sin(math.pi * p) ** 1.5 * 0.85
            return ImageChops.screen(base, Image.blend(Image.new("RGB", (W, H)), self._leak(W, H), amt))
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
#
# Long-form strategy: the timeline is cut into chunks aligned to chapter starts
# (no chunk spans two chapters) and at most CHUNK_SECONDS long. Each chunk's
# output file is named by a content hash of everything that can affect its
# pixels (scenes and captions it overlaps, overlays, theme, assets, encoder
# settings), so re-running after an edit re-renders only the chunks that
# changed. Chunks are retried, a failing frame is replaced by a safe frame and
# recorded (QC fails on it), and progress is written to render/progress.json.

RENDER_VERSION = "3"
CHUNK_SECONDS = 20.0
_G: dict = {}


def _encode_cmd(plan: Plan, out: Path, crf: int, preset: str, threads: int) -> list[str]:
    return [
        "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{plan.width}x{plan.height}", "-r", str(plan.fps), "-i", "-",
        "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-threads", str(threads),
        # cap the peak rate (YouTube recommends ~8-12 Mb/s at 1080p30): film grain otherwise explodes the size
        "-maxrate", f"{max(6, round(14 * plan.width * plan.height / (1920 * 1080)))}M",
        "-bufsize", f"{max(12, round(28 * plan.width * plan.height / (1920 * 1080)))}M",
        "-g", str(plan.fps * 2), "-profile:v", "high",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-movflags", "+faststart", str(out),
    ]


def chunk_plan(plan: Plan, chunk_seconds: float = CHUNK_SECONDS) -> list[tuple[int, int, int]]:
    """[(chapter_index, first_frame, end_frame)] aligned to chapter boundaries."""
    fps = plan.fps
    total = int(math.ceil(plan.duration * fps))
    marks = sorted({0, *[int(round(c.start * fps)) for c in plan.chapters if 0 < c.start < plan.duration]})
    out = []
    for ci, a in enumerate(marks):
        b = marks[ci + 1] if ci + 1 < len(marks) else total
        n = max(1, int(math.ceil((b - a) / (chunk_seconds * fps))))
        for k in range(n):
            f0, f1 = a + (b - a) * k // n, a + (b - a) * (k + 1) // n
            if f1 > f0:
                out.append((ci, f0, f1))
    return out


def _file_sig(path: str) -> str:
    try:
        st = os.stat(path)
        return f"{st.st_size}:{int(st.st_mtime)}"
    except OSError:
        return "missing"


def chunk_key(plan: Plan, theme_data: dict, f0: int, f1: int, crf: int, preset: str) -> str:
    t0, t1 = f0 / plan.fps - 1.0, f1 / plan.fps + 1.0
    scenes = [sc.model_dump() for sc in plan.scenes if sc.end > t0 and sc.start < t1]
    aids = {sc["asset_id"] for sc in scenes if sc.get("asset_id")}
    assets = {a: (plan.assets[a].model_dump(), _file_sig(plan.assets[a].path)) for a in sorted(aids) if a in plan.assets}
    caps = [c.model_dump() for c in plan.captions if c.end > t0 and c.start < t1]
    blob = json.dumps({
        "v": RENDER_VERSION, "f": [f0, f1], "wh": [plan.width, plan.height, plan.fps], "enc": [crf, preset],
        "theme": theme_data, "style": plan.caption_style, "scenes": scenes, "assets": assets, "caps": caps,
        "chapters": [c.model_dump() for c in plan.chapters], "dur": plan.duration, "banner": plan.banner_at,
        "channel": plan.channel_name, "extra": getattr(plan, "extra", None),
    }, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:20]


def _render_chunk(args) -> dict:
    idx, f0, f1, out = args
    plan: Plan = _G["plan"]
    tl = Timeline(plan, _G["theme"])
    tmp = Path(out).with_suffix(".part.mp4")
    proc = subprocess.Popen(_encode_cmd(plan, tmp, _G["crf"], _G["preset"], _G["threads"]),
                            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    t0 = time.time()
    errors: list[str] = []
    last: Optional[Image.Image] = None
    try:
        for fi in range(f0, f1):
            try:
                img = tl.frame(fi / plan.fps, fi)
            except Exception as e:  # keep the chunk alive; QC reports it
                errors.append(f"frame {fi}: {type(e).__name__}: {e}")
                img = last if last is not None else Image.new("RGB", (plan.width, plan.height), tl.th.rgb("bg"))
            last = img
            proc.stdin.write(img.tobytes())
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        proc.wait()
        tl.close()
    if proc.returncode != 0:
        raise RuntimeError(f"x264 failed on chunk {idx}: {err[-300:]}")
    os.replace(tmp, out)
    return {"idx": idx, "frames": f1 - f0, "seconds": round(time.time() - t0, 2), "errors": errors[:20], "n_errors": len(errors)}


def render(plan: Plan, theme_data: dict, work_dir: Path, audio: Optional[Path], out_path: Path,
           workers: int = 4, crf: int = 17, preset: str = "veryfast", chapters: Optional[set[int]] = None,
           export_chapters: bool = False, retries: int = 2) -> Path:
    """Render (or incrementally re-render) the plan. With `chapters`, only those chapters'
    chunks are rendered and exported as separate files (no full assembly)."""
    work_dir.mkdir(parents=True, exist_ok=True)
    seg_dir = work_dir / "segments"
    seg_dir.mkdir(exist_ok=True)
    cplan = chunk_plan(plan)
    keys = [chunk_key(plan, theme_data, f0, f1, crf, preset) for _, f0, f1 in cplan]
    jobs_all = [(k, f0, f1, str(seg_dir / f"seg_{keys[k]}.mp4"), ci) for k, (ci, f0, f1) in enumerate(cplan)]
    wanted = [j for j in jobs_all if chapters is None or j[4] in chapters]
    todo = [j[:4] for j in wanted if not Path(j[3]).exists()]
    total_frames = sum(j[2] - j[1] for j in wanted)
    todo_frames = sum(j[2] - j[1] for j in todo)
    workers = max(1, min(workers, len(todo) or 1))
    _G.update(plan=plan, theme=theme_data, crf=crf, preset=preset, threads=max(1, (os.cpu_count() or 2) // workers))
    L.info("render: %d chunks (%d cached), %d/%d frames to draw at %dx%d@%d on %d workers",
           len(wanted), len(wanted) - len(todo), todo_frames, total_frames, plan.width, plan.height, plan.fps, workers)
    progress = work_dir / "progress.json"
    stats: dict[int, dict] = {}
    t0 = time.time()
    done_frames = 0

    def note(st: dict) -> None:
        nonlocal done_frames
        stats[st["idx"]] = st
        done_frames += st["frames"]
        el = max(1e-3, time.time() - t0)
        fps = done_frames / el
        eta = (todo_frames - done_frames) / max(fps, 1e-3)
        progress.write_text(json.dumps({"done_frames": done_frames, "todo_frames": todo_frames, "fps": round(fps, 1),
                                        "eta_seconds": round(eta), "chunks_done": len(stats), "chunks": len(todo)}))
        L.info("chunk %d/%d (%.0f%%, %.1f fps, eta %s)%s", len(stats), len(todo), 100 * done_frames / max(1, todo_frames),
               fps, time.strftime("%H:%M:%S", time.gmtime(eta)), f" [{st['n_errors']} frame errors]" if st["n_errors"] else "")

    pending = list(todo)
    for attempt in range(retries + 1):
        if not pending:
            break
        failed = []
        if workers == 1 or len(pending) == 1:
            for j in pending:
                try:
                    note(_render_chunk(j))
                except Exception as e:
                    L.warning("chunk %d failed (attempt %d): %s", j[0], attempt + 1, e)
                    failed.append(j)
        else:
            with mp.get_context("fork").Pool(workers) as pool:
                results = {j[0]: pool.apply_async(_render_chunk, (j,)) for j in pending}
                for j in pending:
                    try:
                        note(results[j[0]].get())
                    except Exception as e:
                        L.warning("chunk %d failed (attempt %d): %s", j[0], attempt + 1, e)
                        failed.append(j)
        pending = failed
    if pending:
        raise RuntimeError(f"{len(pending)} chunks failed after {retries + 1} attempts: {[j[0] for j in pending]}")

    manifest = {"version": RENDER_VERSION, "chunks": [
        {"idx": j[0], "chapter": j[4], "frames": [j[1], j[2]], "file": Path(j[3]).name,
         "errors": stats.get(j[0], {}).get("errors", []), "n_errors": stats.get(j[0], {}).get("n_errors", 0)}
        for j in wanted]}
    old = json.loads((work_dir / "chunks.json").read_text()) if (work_dir / "chunks.json").exists() else {"chunks": []}
    prev_err = {c["file"]: c for c in old.get("chunks", [])}
    for c in manifest["chunks"]:  # keep error records of cached chunks
        if c["file"] in prev_err and not c["n_errors"]:
            c["errors"], c["n_errors"] = prev_err[c["file"]].get("errors", []), prev_err[c["file"]].get("n_errors", 0)
    if chapters is None:
        (work_dir / "chunks.json").write_text(json.dumps(manifest, indent=1))
        live = {Path(j[3]).name for j in jobs_all}
        for f in seg_dir.glob("seg_*.mp4"):  # garbage-collect stale segments
            if f.name not in live:
                f.unlink()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if chapters is None:
        _assemble([j for j in wanted], audio, out_path, seg_dir, None)
        L.info("video written: %s (%.1fs)", out_path, time.time() - t0)
    if export_chapters or chapters is not None:
        cdir = out_path.parent / "chapters"
        cdir.mkdir(exist_ok=True)
        for ci in sorted({j[4] for j in wanted}):
            cj = [j for j in wanted if j[4] == ci]
            a, b = cj[0][1] / plan.fps, cj[-1][2] / plan.fps
            dest = cdir / f"chapter_{ci:02d}.mp4"
            _assemble(cj, audio, dest, seg_dir, (a, b - a))
            L.info("chapter %d exported: %s", ci, dest)
    return out_path


def _assemble(jobs, audio: Optional[Path], dest: Path, seg_dir: Path, window: Optional[tuple[float, float]]) -> None:
    lst = seg_dir / f"list_{dest.stem}.txt"
    lst.write_text("".join(f"file '{Path(j[3]).name}'\n" for j in jobs))
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst)]
    if audio and Path(audio).exists():
        if window:
            cmd += ["-ss", f"{window[0]:.3f}", "-t", f"{window[1]:.3f}"]
        cmd += ["-i", str(audio), "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += ["-c:v", "copy", "-movflags", "+faststart", "-shortest", str(dest)]
    subprocess.run(cmd, check=True)


def still(plan: Plan, theme_data: dict, t: float, captions: bool = True) -> Image.Image:
    tl = Timeline(plan, theme_data, captions=captions)
    try:
        return tl.frame(t, int(t * plan.fps))
    finally:
        tl.close()
