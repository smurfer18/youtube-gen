"""Scene renderers. Each renderer is created once per scene per worker,
prepares its static layers, then returns a full RGB frame for any local time.

Kinds: footage (image Ken Burns / photo card / video clip), chapter, graphic
(big_number, bar_chart, line_chart, timeline, list, quote, compare, kinetic),
kinetic, outro. Add a type by subclassing ``Renderer`` and registering it in
``GRAPHICS`` or ``KINDS``.
"""

from __future__ import annotations

import math
import subprocess
from collections import OrderedDict
from typing import Optional

from PIL import Image, ImageDraw, ImageOps

from ytgen.models import Asset, Plan, Scene
from ytgen.render import fx
from ytgen.render.theme import Theme
from ytgen.util.text import format_figure, parse_figure


# --------------------------------------------------------------- sources ----


class MediaCache:
    """Per-worker cache of graded, cover-sized source images."""

    def __init__(self, plan: Plan, theme: Theme, size: int = 6):
        self.plan, self.th = plan, theme
        self.items: OrderedDict[str, Image.Image] = OrderedDict()
        self.size = size

    def asset(self, aid: Optional[str]) -> Optional[Asset]:
        return self.plan.assets.get(aid) if aid else None

    def graded(self, aid: str, overscan: float = 1.25) -> Optional[Image.Image]:
        key = f"{aid}@{overscan}"
        if key in self.items:
            self.items.move_to_end(key)
            return self.items[key]
        a = self.asset(aid)
        if not a or not a.path or a.kind != "image":
            return None
        try:
            with Image.open(a.path) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                if overscan:
                    im = fx.cover(im, self.th.W, self.th.H, overscan)
                else:  # natural size, bounded
                    im.thumbnail((self.th.W * 1.1, self.th.H * 1.1), Image.LANCZOS)
                img = fx.grade(im, self.th.d.get("grade", {}))
        except Exception:
            return None
        self.items[key] = img
        if len(self.items) > self.size:
            self.items.popitem(last=False)
        return img


class VideoReader:
    """Sequential frame reader for a clip, cover-cropped to the frame; loops."""

    def __init__(self, path: str, w: int, h: int, fps: int, duration: float):
        self.path, self.w, self.h, self.fps, self.dur = path, w, h, fps, max(0.5, duration)
        self.proc: Optional[subprocess.Popen] = None
        self.pos = -1
        self.last: Optional[Image.Image] = None

    def _open(self, t: float) -> None:
        self.close()
        t = t % self.dur
        vf = f"scale={self.w}:{self.h}:force_original_aspect_ratio=increase,crop={self.w}:{self.h},fps={self.fps}"
        self.proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-stream_loop", "-1", "-i", self.path, "-vf", vf,
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.pos = int(round(t * self.fps)) - 1

    def frame(self, t: float) -> Optional[Image.Image]:
        want = int(round(t * self.fps))
        if self.proc is None or want < self.pos or want - self.pos > 3:
            self._open(t)
        n = self.w * self.h * 3
        while self.pos < want:
            buf = self.proc.stdout.read(n) if self.proc and self.proc.stdout else b""
            if len(buf) < n:
                break
            self.last = Image.frombytes("RGB", (self.w, self.h), buf)
            self.pos += 1
        return self.last

    def close(self) -> None:
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc = None


# -------------------------------------------------------------- renderers ---


class Renderer:
    def __init__(self, sc: Scene, plan: Plan, th: Theme, media: MediaCache):
        self.sc, self.plan, self.th, self.media = sc, plan, th, media
        self.W, self.H = th.W, th.H
        self.dur = max(0.04, sc.end - sc.start)

    def frame(self, lt: float) -> Image.Image:  # pragma: no cover - abstract
        raise NotImplementedError

    def close(self) -> None:
        pass

    # helpers
    def backdrop(self, dim: float = 0.35, blur: int = 28, mix: float = 0.55) -> Image.Image:
        """Theme background, optionally mixed with a blurred, dimmed shot."""
        bg = fx.make_background(self.th).copy()
        img = self.media.graded(self.sc.asset_id, 1.0) if self.sc.asset_id else None
        if img is not None:
            b = fx.blur_backdrop(img, self.W, self.H, blur, dim)
            bg = Image.blend(bg, b, mix)
        return bg

    def title_block(self, canvas: Image.Image, lt: float, title: str, y: float | None = None) -> float:
        if not title:
            return self.H * 0.12
        th = self.th
        x = th.s(120) if not th.vertical else th.s(80)
        y = th.H * (0.12 if not th.vertical else 0.14) if y is None else y
        p = fx.ease_out(fx.progress(lt, 0.05, 0.5))
        t = title.upper() if th.upper("display") else title
        f = fx.fit_font(th, "display", t, 60, self.W - x * 2)
        lay = fx.text_layer(t, f, th.color("ink"))
        d = ImageDraw.Draw(canvas, "RGBA")
        d.rectangle((x, y - th.s(18), x + th.s(70) * p, y - th.s(12)), fill=th.color("accent"))
        fx.paste(canvas, lay, (x, y + (1 - p) * th.s(20)), opacity=p)
        return y + lay.height + th.s(20)

    def source_footer(self, canvas: Image.Image, lt: float, text: str) -> None:
        if not text:
            return
        th = self.th
        lay = fx.text_layer(text, th.font("body", 22), th.color("muted"))
        fx.paste(canvas, lay, (th.s(120) if not th.vertical else th.s(80), self.H - th.s(170) - lay.height),
                 opacity=fx.progress(lt, 0.6, 0.5) * 0.9)


class FootageImage(Renderer):
    def __init__(self, *a):
        super().__init__(*a)
        m = self.sc.motion
        strength = self.th.d.get("motion", {}).get("kenburns", 1.0)
        self.z0 = 1 + (m.zoom_from - 1) * strength
        self.z1 = 1 + (m.zoom_to - 1) * strength
        self.src = self.media.graded(self.sc.asset_id, 1.25)

    def frame(self, lt: float) -> Image.Image:
        img = self.src
        if img is None:
            return fx.make_background(self.th).copy()
        m = self.sc.motion
        p = fx.ease_in_out(fx.clamp(lt / self.dur)) * 0.85 + fx.clamp(lt / self.dur) * 0.15
        z = fx.lerp(self.z0, self.z1, p)
        cx = fx.lerp(m.pan_from[0], m.pan_to[0], p) * img.width
        cy = fx.lerp(m.pan_from[1], m.pan_to[1], p) * img.height
        ar = self.W / self.H
        bw = min(img.width, img.height * ar)
        bh = bw / ar
        ww, hh = bw / z, bh / z
        x0 = fx.clamp(cx - ww / 2, 0, img.width - ww)
        y0 = fx.clamp(cy - hh / 2, 0, img.height - hh)
        return img.resize((self.W, self.H), Image.BILINEAR, box=(x0, y0, x0 + ww, y0 + hh), reducing_gap=None)


class FootageCard(Renderer):
    """A photo 'print' laid on a blurred copy of itself: for portrait or low-res images."""

    def __init__(self, *a):
        super().__init__(*a)
        th = self.th
        src = self.media.graded(self.sc.asset_id, 0)
        self.ok = src is not None
        if not self.ok:
            return
        self.back = fx.blur_backdrop(src, self.W, self.H, 40, 0.42)
        self.back = Image.blend(self.back, fx.make_background(th), 0.25)
        fr = th.d.get("frame", {})
        style = fr.get("style", "print")
        max_w, max_h = self.W * (0.62 if not th.vertical else 0.84), self.H * (0.70 if not th.vertical else 0.52)
        s = min(max_w / src.width, max_h / src.height)
        photo = src.resize((max(1, int(src.width * s)), max(1, int(src.height * s))), Image.LANCZOS).convert("RGBA")
        if style == "print":
            b = max(4, int(self.W * fr.get("border", 0.012)))
            card = Image.new("RGBA", (photo.width + b * 2, photo.height + b * 2), (246, 243, 236, 255))
            card.alpha_composite(photo, (b, b))
        elif style == "glow":
            b = max(2, int(self.W * fr.get("border", 0.0025)))
            card = Image.new("RGBA", (photo.width + b * 2, photo.height + b * 2), th.color("accent"))
            card.alpha_composite(photo, (b, b))
        else:
            r = int(self.W * fr.get("radius", 0.012))
            mask = fx.rounded(photo.size, r, (255, 255, 255, 255)).getchannel("A")
            photo.putalpha(mask)
            card = photo
        tilt = self.sc.motion.tilt if style == "print" else 0
        if tilt:
            card = card.rotate(tilt, Image.BICUBIC, expand=True)
        blur = th.s(26)
        sh = fx.shadow(card, blur, fr.get("shadow", 0.6))
        if style == "glow":
            glow = fx.shadow(card, th.s(18), 0.5)
            tint = Image.new("RGBA", glow.size, th.color("accent"))
            tint.putalpha(glow.getchannel("A"))
            sh = tint
        pad = blur * 2
        full = Image.new("RGBA", sh.size, (0, 0, 0, 0))
        full.alpha_composite(sh, (0, th.s(14)))
        full.alpha_composite(card, (pad, pad))
        if fr.get("crop_marks"):
            d = ImageDraw.Draw(full)
            L, g = th.s(26), th.s(10)
            x0, y0, x1, y1 = pad - g, pad - g, pad + card.width + g, pad + card.height + g
            col = th.color("accent")
            for (x, y, dx, dy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
                d.line((x, y, x + dx * L, y), fill=col, width=th.s(3))
                d.line((x, y, x, y + dy * L), fill=col, width=th.s(3))
        self.card = full

    def frame(self, lt: float) -> Image.Image:
        if not self.ok:
            return fx.make_background(self.th).copy()
        q = fx.clamp(lt / self.dur)
        # parallax: the blurred backdrop drifts and zooms one way, the print the other
        z = 1.06 + 0.04 * q
        bw, bh = self.W / z, self.H / z
        dx = (0.5 - q) * (self.W - bw) * 0.9
        f = self.back.resize((self.W, self.H), Image.BILINEAR,
                             box=((self.W - bw) / 2 + dx, (self.H - bh) / 2, (self.W + bw) / 2 + dx, (self.H + bh) / 2))
        p = fx.ease_out(fx.progress(lt, 0.0, 0.65))
        drift = 1.0 + 0.035 * q
        x = self.W / 2 + (q - 0.5) * self.th.s(36)
        y = self.H / 2 - (1 - p) * self.th.s(60) - self.th.s(20 if not self.th.vertical else 120)
        fx.paste(f, self.card, (x, y), opacity=min(1, p * 1.3), scale=drift, anchor="mm")
        return f


class FootageVideo(Renderer):
    def __init__(self, *a):
        super().__init__(*a)
        a_ = self.media.asset(self.sc.asset_id)
        self.reader = VideoReader(a_.path, self.W, self.H, self.plan.fps, a_.duration or self.dur) if a_ else None
        self.g = self.th.d.get("grade", {})

    def frame(self, lt: float) -> Image.Image:
        img = self.reader.frame(max(0.0, lt)) if self.reader else None
        if img is None:
            return fx.make_background(self.th).copy()
        return fx.grade(img, self.g)

    def close(self) -> None:
        if self.reader:
            self.reader.close()


class Chapter(Renderer):
    def __init__(self, *a):
        super().__init__(*a)
        th = self.th
        self.src = self.media.graded(self.sc.asset_id, 1.25) if self.sc.asset_id else None
        title = self.sc.title.upper() if th.upper("display") else self.sc.title
        f = fx.fit_font(th, "display", title, 118 if not th.vertical else 104, self.W * 0.8)
        lines = fx.wrap(title, f, self.W * 0.8, 2)
        self.title_layers = [fx.text_layer(l, f, th.color("ink"), shadow_blur=th.s(16), shadow_opacity=0.5) for l in lines]
        sub = self.sc.subtitle.upper()
        self.sub = fx.text_layer(sub, th.font("body", 30), th.color("accent"), tracking=0.32)
        self.scrim = Image.new("RGB", (self.W, self.H), th.rgb("bg"))

    def frame(self, lt: float) -> Image.Image:
        th = self.th
        if self.src is not None:
            img = self.src
            z = 1.0 + 0.06 * fx.clamp(lt / max(self.dur, 3))
            ar = self.W / self.H
            bw = min(img.width, img.height * ar)
            bh = bw / ar
            ww, hh = bw / z, bh / z
            x0, y0 = (img.width - ww) / 2, (img.height - hh) / 2
            f = img.resize((self.W, self.H), Image.BILINEAR, box=(x0, y0, x0 + ww, y0 + hh))
            f = Image.blend(f, self.scrim, 0.62)
        else:
            f = fx.make_background(th).copy()
        cy = self.H * 0.47
        total_h = sum(l.height for l in self.title_layers)
        p_sub = fx.ease_out(fx.progress(lt, 0.0, 0.45))
        fx.paste(f, self.sub, (self.W / 2, cy - total_h / 2 - th.s(34)), opacity=p_sub, anchor="mb")
        y = cy - total_h / 2
        for i, lay in enumerate(self.title_layers):
            p = fx.ease_out(fx.progress(lt, 0.15 + 0.08 * i, 0.55))
            fx.paste(f, lay, (self.W / 2, y + (1 - p) * th.s(40)), opacity=p, anchor="mt")
            y += lay.height
        p_rule = fx.ease_in_out(fx.progress(lt, 0.4, 0.6))
        d = ImageDraw.Draw(f)
        rw = th.s(220) * p_rule
        d.rectangle((self.W / 2 - rw / 2, y + th.s(26), self.W / 2 + rw / 2, y + th.s(32)), fill=th.color("accent"))
        return f


class Outro(Renderer):
    def __init__(self, *a):
        super().__init__(*a)
        th = self.th
        self.base = self.backdrop(dim=0.4, mix=0.7)
        name = self.sc.title or "Subscribe"
        self.name = fx.text_layer(name, fx.fit_font(th, "display", name, 84, self.W * 0.8), th.color("ink"))
        self.thanks = fx.text_layer("Thanks for watching", th.font("accent", 46), th.color("muted"))
        btn_t = fx.text_layer("SUBSCRIBE", th.font("body", 38), (255, 255, 255, 255), tracking=0.08)
        self.btn = fx.rounded((btn_t.width + th.s(80), btn_t.height + th.s(34)), th.s(14), (204, 0, 0, 255))
        self.btn.alpha_composite(btn_t, (th.s(40), th.s(17)))

    def frame(self, lt: float) -> Image.Image:
        f = self.base.copy()
        th = self.th
        p1 = fx.ease_out(fx.progress(lt, 0.1, 0.6))
        p2 = fx.ease_out(fx.progress(lt, 0.4, 0.6))
        p3 = fx.ease_out_back(fx.progress(lt, 0.8, 0.5))
        cy = self.H * 0.44
        fx.paste(f, self.thanks, (self.W / 2, cy - th.s(90)), opacity=p1, anchor="mm")
        fx.paste(f, self.name, (self.W / 2, cy + (1 - p2) * th.s(30)), opacity=p2, anchor="mm")
        pulse = 1 + 0.025 * math.sin(lt * 4) * fx.progress(lt, 1.4, 0.3)
        fx.paste(f, self.btn, (self.W / 2, cy + th.s(130)), opacity=min(1, p3 * 1.2), scale=max(0.01, p3) * pulse, anchor="mm")
        return f


class HtmlClip(Renderer):
    """A pre-rendered motion clip (HyperFrames / Playwright), played frame-accurately."""

    def __init__(self, *a):
        super().__init__(*a)
        self.reader = VideoReader(self.sc.clip_path, self.W, self.H, self.plan.fps, self.dur + 1)

    def frame(self, lt: float) -> Image.Image:
        img = self.reader.frame(min(max(0.0, lt), self.dur - 0.5 / self.plan.fps))
        return img.copy() if img is not None else fx.make_background(self.th).copy()

    def close(self) -> None:
        self.reader.close()


class TitleCard(Renderer):
    """Built-in title sequence (fallback when HyperFrames is unavailable)."""

    def __init__(self, *a):
        super().__init__(*a)
        th = self.th
        self.src = self.media.graded(self.sc.asset_id, 1.25) if self.sc.asset_id else None
        title = self.sc.title.upper() if th.upper("display") else self.sc.title
        f = fx.fit_font(th, "display", title, 120 if not th.vertical else 100, self.W * 0.84)
        self.lines = [fx.text_layer(l, f, th.color("ink"), shadow_blur=th.s(18), shadow_opacity=0.55)
                      for l in fx.wrap(title, f, self.W * 0.84, 3)]
        self.kicker = fx.text_layer((self.sc.subtitle or "").upper(), th.font("body", 28), th.color("accent"), tracking=0.4)
        self.dark = Image.new("RGB", (self.W, self.H), th.rgb("bg"))

    def frame(self, lt: float) -> Image.Image:
        th = self.th
        if self.src is not None:
            img = self.src
            z = 1.12 - 0.08 * fx.ease_out(fx.clamp(lt / self.dur))
            ar = self.W / self.H
            bw = min(img.width, img.height * ar)
            ww, hh = bw / z, bw / ar / z
            x0, y0 = (img.width - ww) / 2, (img.height - hh) / 2
            f = img.resize((self.W, self.H), Image.BILINEAR, box=(x0, y0, x0 + ww, y0 + hh))
            f = Image.blend(f, self.dark, 0.66 - 0.06 * fx.progress(lt, 0, 1.2))
        else:
            f = fx.make_background(th).copy()
        out = 1 - fx.ease_in_out(fx.progress(lt, self.dur - 0.6, 0.55))
        fx.paste(f, self.kicker, (self.W / 2, self.H * 0.38), opacity=fx.ease_out(fx.progress(lt, 0.2, 0.6)) * out, anchor="mm")
        y = self.H * 0.44
        for i, l in enumerate(self.lines):
            p = fx.ease_out(fx.progress(lt, 0.4 + i * 0.12, 0.7))
            fx.paste(f, l, (self.W / 2, y + (1 - p) * th.s(46)), opacity=p * out, scale=1.06 - 0.06 * p, anchor="mt")
            y += l.height * 0.95
        rw = th.s(240) * fx.ease_in_out(fx.progress(lt, 0.9, 0.8)) * out
        ImageDraw.Draw(f).rectangle((self.W / 2 - rw / 2, y + th.s(24), self.W / 2 + rw / 2, y + th.s(30)), fill=th.color("accent"))
        return f


# ---------------------------------------------------------------- graphics --


class Graphic(Renderer):
    def __init__(self, *a):
        super().__init__(*a)
        self.g = self.sc.graphic
        self.base = self.backdrop(dim=0.32, mix=0.5)
        self.beats = [t - self.sc.start for t in (self.sc.beat_times or [])] or [0.3]

    def beat(self, i: int) -> float:
        if i < len(self.beats):
            return self.beats[i]
        span = self.dur * 0.6
        n = max(1, len(self.g.items))
        return 0.4 + span * i / n


class BigNumber(Graphic):
    def __init__(self, *a):
        super().__init__(*a)
        g = self.g
        self.fig = parse_figure(g.display) if g.display else None
        if self.fig is None and g.value is not None:
            self.fig = (g.value, g.prefix, g.suffix, 0 if float(g.value).is_integer() else 1, abs(g.value) >= 10000)
        th = self.th
        final = g.display or (format_figure(g.value or 0) if g.value is not None else "")
        self.num_font = fx.fit_font(th, "numbers", final, 300 if not th.vertical else 260, self.W * 0.82)
        lab = g.label or g.unit
        self.label = fx.text_layer(lab, fx.fit_font(th, "accent", lab, 58, self.W * 0.8), th.color("muted")) if lab else None

    def frame(self, lt: float) -> Image.Image:
        th = self.th
        f = self.base.copy()
        self.title_block(f, lt, self.g.title)
        t0 = self.beat(0)
        p = fx.ease_out(fx.progress(lt, t0, 1.25))
        if lt >= t0 - 0.05:
            if self.fig and self.fig[0] is not None:
                v, pre, suf, dec, commas = self.fig
                txt = pre + format_figure(v * p, dec, commas) + suf
            else:
                txt = self.g.display or ""
            lay = fx.text_layer(txt, self.num_font, th.color("ink"), shadow_blur=th.s(18), shadow_opacity=0.45)
            pop = fx.ease_out_back(fx.progress(lt, t0, 0.35))
            cy = self.H * (0.44 if not th.vertical else 0.36)
            fx.paste(f, lay, (self.W / 2, cy), opacity=min(1, pop * 1.5), scale=0.85 + 0.15 * pop, anchor="mm")
            d = ImageDraw.Draw(f)
            rw = th.s(260) * fx.ease_in_out(fx.progress(lt, t0 + 0.2, 0.7))
            yb = cy + lay.height / 2 + th.s(10)
            d.rectangle((self.W / 2 - rw / 2, yb, self.W / 2 + rw / 2, yb + th.s(7)), fill=th.color("accent"))
            if self.label:
                pl = fx.ease_out(fx.progress(lt, t0 + 0.3, 0.5))
                fx.paste(f, self.label, (self.W / 2, yb + th.s(40) + (1 - pl) * th.s(20)), opacity=pl, anchor="mt")
        self.source_footer(f, lt, self.g.source)
        return f


class BarChart(Graphic):
    def __init__(self, *a):
        super().__init__(*a)
        th = self.th
        items = [i for i in self.g.items if i.value is not None][:7]
        self.items = items
        self.vmax = max((abs(i.value) for i in items), default=1) or 1
        self.lab_font = th.font("body", 34 if not th.vertical else 36)
        self.val_font = th.font("numbers", 40)
        self.top_idx = max(range(len(items)), key=lambda k: items[k].value) if items else -1
        self.labels = [fx.text_layer(i.label, self.lab_font, th.color("ink")) for i in items]

    def frame(self, lt: float) -> Image.Image:
        th = self.th
        f = self.base.copy()
        y0 = self.title_block(f, lt, self.g.title) + th.s(30)
        n = max(1, len(self.items))
        x0 = th.s(120) if not th.vertical else th.s(70)
        x1 = self.W - x0
        lab_w = min(max((l.width for l in self.labels), default=0) + th.s(30), (x1 - x0) * 0.38)
        avail_h = self.H * (0.8 if not th.vertical else 0.58) - y0
        row = min(th.s(96), avail_h / n)
        bar_h = row * 0.56
        d = ImageDraw.Draw(f, "RGBA")
        bx0, bx1 = x0 + lab_w, x1 - th.s(150)
        for k, it in enumerate(self.items):
            tb = self.beat(k)
            p = fx.ease_out(fx.progress(lt, tb, 0.6))
            if lt < tb - 0.2:
                continue
            y = y0 + k * row
            fx.paste(f, self.labels[k], (x0, y + bar_h / 2), opacity=fx.progress(lt, tb - 0.2, 0.3), anchor="lm")
            w = (bx1 - bx0) * (abs(it.value) / self.vmax) * p
            col = th.color("accent") if k == self.top_idx else th.color("chart_base")
            if k != self.top_idx:
                col = tuple(min(255, c + 40) for c in col[:3]) + (255,)
            d.rounded_rectangle((bx0, y, bx0 + max(w, 2), y + bar_h), radius=th.s(6), fill=col)
            fig = parse_figure(it.display) if it.display else None
            if fig:
                v, pre, suf, dec, commas = fig
                txt = pre + format_figure(v * p, dec, commas) + suf
            else:
                txt = format_figure(it.value * p, 0 if float(it.value).is_integer() else 1) + (f" {self.g.unit}" if self.g.unit else "")
            vl = fx.text_layer(txt, self.val_font, th.color("ink"))
            fx.paste(f, vl, (bx0 + w + th.s(16), y + bar_h / 2), opacity=p, anchor="lm")
        self.source_footer(f, lt, self.g.source)
        return f


class LineChart(Graphic):
    def __init__(self, *a):
        super().__init__(*a)
        th = self.th
        self.items = [i for i in self.g.items if i.value is not None][:12]
        vals = [i.value for i in self.items] or [0, 1]
        lo, hi = min(vals), max(vals)
        pad = (hi - lo) * 0.15 or 1
        self.lo, self.hi = lo - pad, hi + pad
        self.xl = [fx.text_layer(i.label, th.font("body", 26), th.color("muted")) for i in self.items]
        self.vf = th.font("numbers", 32)

    def frame(self, lt: float) -> Image.Image:
        th = self.th
        f = self.base.copy()
        y_top = self.title_block(f, lt, self.g.title) + th.s(60)
        x0, x1 = th.s(150), self.W - th.s(150)
        y1 = self.H * (0.74 if not th.vertical else 0.56)
        n = len(self.items)
        if n < 2:
            return f
        pts = [(x0 + (x1 - x0) * k / (n - 1), y1 - (y1 - y_top) * (it.value - self.lo) / (self.hi - self.lo))
               for k, it in enumerate(self.items)]
        t0 = self.beat(0)
        p = fx.ease_in_out(fx.progress(lt, t0, min(2.4, self.dur * 0.6)))
        # supersampled line layer for anti-aliasing
        sc = 2
        lay = Image.new("RGBA", (self.W * sc, self.H * sc), (0, 0, 0, 0))
        dl = ImageDraw.Draw(lay)
        for k in range(n):
            dl.line((pts[k][0] * sc, y1 * sc, pts[k][0] * sc, (y1 + th.s(10)) * sc), fill=th.color("muted", 120), width=max(1, th.s(2)) * sc)
        dl.line((x0 * sc, y1 * sc, x1 * sc, y1 * sc), fill=th.color("muted", 140), width=max(1, th.s(2)) * sc)
        upto = p * (n - 1)
        seg = [pts[0]]
        for k in range(1, n):
            if k <= upto:
                seg.append(pts[k])
            else:
                fr = upto - (k - 1)
                if fr > 0:
                    seg.append((fx.lerp(pts[k - 1][0], pts[k][0], fr), fx.lerp(pts[k - 1][1], pts[k][1], fr)))
                break
        if len(seg) > 1:
            dl.line([(x * sc, y * sc) for x, y in seg], fill=th.color("accent"), width=th.s(7) * sc, joint="curve")
        hx, hy = seg[-1]
        r = th.s(11)
        dl.ellipse(((hx - r) * sc, (hy - r) * sc, (hx + r) * sc, (hy + r) * sc), fill=th.color("accent"))
        lay = lay.resize((self.W, self.H), Image.LANCZOS)
        f.paste(lay, (0, 0), lay)
        for k, it in enumerate(self.items):
            fx.paste(f, self.xl[k], (pts[k][0], y1 + th.s(22)), opacity=fx.progress(lt, t0 - 0.3, 0.4), anchor="mt")
            if k <= upto + 0.01 and (k in (0, n - 1) or n <= 7):
                txt = it.display or format_figure(it.value, 0 if float(it.value).is_integer() else 1)
                vl = fx.text_layer(txt, self.vf, th.color("ink"))
                fx.paste(f, vl, (pts[k][0], pts[k][1] - th.s(22)), opacity=1, anchor="mb")
        self.source_footer(f, lt, self.g.source)
        return f


class Timeline(Graphic):
    def __init__(self, *a):
        super().__init__(*a)
        th = self.th
        self.items = self.g.items[:6]
        n = max(1, len(self.items))
        if not th.vertical:
            sp = (self.W * 0.8) / n
            ef = th.font("body", 34)
            self.events = [[fx.text_layer(l, ef, th.color("ink")) for l in fx.wrap(i.display or "", ef, sp * 0.86, 3)] for i in self.items]
        else:
            ef = th.font("body", 44)
            self.events = [[fx.text_layer(l, ef, th.color("ink")) for l in fx.wrap(i.display or "", ef, self.W * 0.62, 2)] for i in self.items]
        yf = th.font("numbers", 66 if not th.vertical else 76)
        self.years = [fx.text_layer(i.label, yf, th.color("accent")) for i in self.items]
        self.years_dim = [fx.text_layer(i.label, yf, th.color("muted")) for i in self.items]

    def frame(self, lt: float) -> Image.Image:
        th = self.th
        f = self.base.copy()
        self.title_block(f, lt, self.g.title)
        d = ImageDraw.Draw(f, "RGBA")
        n = len(self.items)
        if not n:
            return f
        cur = max([k for k in range(n) if lt >= self.beat(k)] or [-1])
        if not th.vertical:
            x0, x1, y = self.W * 0.1, self.W * 0.9, self.H * 0.52
            sp = (x1 - x0) / n
            xs = [x0 + sp * (k + 0.5) for k in range(n)]
            d.line((x0, y, x1, y), fill=th.color("muted", 90), width=th.s(3))
            if cur >= 0:
                tgt = xs[cur] + (sp * 0.5 if cur == n - 1 else 0)
                prog = fx.ease_out(fx.progress(lt, self.beat(cur), 0.6))
                prev = x0 if cur == 0 else xs[cur - 1]
                d.line((x0, y, fx.lerp(prev, tgt, prog), y), fill=th.color("accent"), width=th.s(5))
            for k in range(n):
                tb = self.beat(k)
                if lt < tb:
                    continue
                pk = fx.ease_out_back(fx.progress(lt, tb, 0.35))
                r = th.s(13) * pk
                d.ellipse((xs[k] - r, y - r, xs[k] + r, y + r), fill=th.color("accent") if k == cur else th.color("ink"))
                yl = self.years[k] if k == cur else self.years_dim[k]
                fx.paste(f, yl, (xs[k], y - th.s(34) - (1 - pk) * th.s(16)), opacity=min(1, pk), anchor="mb")
                ey = y + th.s(40)
                for ln in self.events[k]:
                    fx.paste(f, ln, (xs[k], ey), opacity=fx.progress(lt, tb + 0.15, 0.4) * (1 if k == cur else 0.7), anchor="mt")
                    ey += ln.height
        else:
            x, y0, y1 = self.W * 0.16, self.H * 0.22, self.H * 0.6
            sp = (y1 - y0) / n
            ys = [y0 + sp * (k + 0.5) for k in range(n)]
            d.line((x, y0, x, y1), fill=th.color("muted", 90), width=th.s(3))
            for k in range(n):
                tb = self.beat(k)
                if lt < tb:
                    continue
                pk = fx.ease_out_back(fx.progress(lt, tb, 0.35))
                r = th.s(14) * pk
                d.ellipse((x - r, ys[k] - r, x + r, ys[k] + r), fill=th.color("accent") if k == cur else th.color("ink"))
                yl = self.years[k] if k == cur else self.years_dim[k]
                fx.paste(f, yl, (x + th.s(40), ys[k]), opacity=min(1, pk), anchor="lb")
                ey = ys[k] + th.s(6)
                for ln in self.events[k]:
                    fx.paste(f, ln, (x + th.s(40), ey), opacity=fx.progress(lt, tb + 0.15, 0.4), anchor="lt")
                    ey += ln.height
        return f


class ListGraphic(Graphic):
    def __init__(self, *a):
        super().__init__(*a)
        th = self.th
        self.items = self.g.items[:6]
        self.labels = []
        for i in self.items:
            t = i.label.upper() if th.upper("display") else i.label
            f = fx.fit_font(th, "display", t, 54 if not th.vertical else 58, self.W * 0.66)
            self.labels.append((fx.text_layer(t, f, th.color("ink")), fx.text_layer(t, f, th.color("muted"))))
        nf = th.font("numbers", 54)
        self.nums = [fx.text_layer(f"{k + 1:02d}", nf, th.color("accent")) for k in range(len(self.items))]

    def frame(self, lt: float) -> Image.Image:
        th = self.th
        f = self.base.copy()
        y0 = self.title_block(f, lt, self.g.title) + th.s(40)
        n = max(1, len(self.items))
        row = min(th.s(118) if not th.vertical else th.s(150), (self.H * (0.78 if not th.vertical else 0.6) - y0) / n)
        x = th.s(140) if not th.vertical else th.s(80)
        cur = max([k for k in range(n) if lt >= self.beat(k)] or [-1])
        for k in range(len(self.items)):
            tb = self.beat(k)
            if lt < tb:
                continue
            p = fx.ease_out(fx.progress(lt, tb, 0.45))
            y = y0 + k * row
            dx = (1 - p) * th.s(-50)
            fx.paste(f, self.nums[k], (x + dx, y + row / 2), opacity=p, anchor="lm")
            lay = self.labels[k][0] if k == cur else self.labels[k][1]
            fx.paste(f, lay, (x + th.s(120) + dx, y + row / 2), opacity=p, anchor="lm")
        return f


class Quote(Graphic):
    def __init__(self, *a):
        super().__init__(*a)
        th = self.th
        qf = th.font("accent", 66 if not th.vertical else 64)
        self.lines = [fx.text_layer(l, qf, th.color("ink")) for l in fx.wrap(self.g.title or "", qf, self.W * 0.72, 5)]
        self.mark = fx.text_layer("“", th.font("accent", 260), th.color("accent"))
        self.author = fx.text_layer(f"— {self.g.label}", th.font("body", 36), th.color("muted")) if self.g.label else None

    def frame(self, lt: float) -> Image.Image:
        th = self.th
        f = self.base.copy()
        t0 = self.beat(0)
        total = sum(l.height for l in self.lines)
        y = self.H * (0.47 if not th.vertical else 0.40) - total / 2
        pm = fx.ease_out(fx.progress(lt, max(0, t0 - 0.3), 0.4))
        if th.vertical:
            fx.paste(f, self.mark, (self.W / 2, y - th.s(10)), opacity=pm, anchor="mb")
        else:
            x_first = (self.W - (self.lines[0].width if self.lines else 0)) / 2
            fx.paste(f, self.mark, (min(self.W * 0.14, x_first - self.mark.width * 0.8), y - th.s(70)), opacity=pm)
        for i, l in enumerate(self.lines):
            p = fx.ease_out(fx.progress(lt, t0 + i * 0.22, 0.5))
            fx.paste(f, l, (self.W / 2, y + (1 - p) * th.s(16)), opacity=p, anchor="mt")
            y += l.height
        if self.author:
            pa = fx.ease_out(fx.progress(lt, t0 + len(self.lines) * 0.22 + 0.2, 0.5))
            fx.paste(f, self.author, (self.W / 2, y + th.s(36)), opacity=pa, anchor="mt")
        return f


class Compare(Graphic):
    def __init__(self, *a):
        super().__init__(*a)
        th = self.th
        self.items = (self.g.items + [self.g.items[-1]] if len(self.g.items) == 1 else self.g.items)[:2]
        self.lf = th.font("display", 52)
        self.vf = th.font("numbers", 120 if not th.vertical else 110)
        self.vs = fx.text_layer("VS", th.font("display", 46), th.color("on_accent"))

    def frame(self, lt: float) -> Image.Image:
        th = self.th
        f = self.base.copy()
        self.title_block(f, lt, self.g.title)
        if len(self.items) < 2:
            return f
        centers = ([(self.W * 0.28, self.H * 0.5), (self.W * 0.72, self.H * 0.5)] if not th.vertical
                   else [(self.W / 2, self.H * 0.23), (self.W / 2, self.H * 0.53)])
        for k, it in enumerate(self.items):
            tb = self.beat(k) if len(self.beats) > 1 else self.beat(0) + 0.35 * k
            p = fx.ease_out(fx.progress(lt, tb, 0.55))
            if lt < tb:
                continue
            cx, cy = centers[k]
            off = (1 - p) * th.s(60) * (-1 if k == 0 else 1)
            label = it.label.upper() if th.upper("display") else it.label
            fig = parse_figure(it.display) if it.display else None
            if fig:
                v, pre, suf, dec, commas = fig
                txt = pre + format_figure(v * p, dec, commas) + suf
            elif it.value is not None:
                txt = format_figure(it.value * p)
            else:
                txt = it.display or ""
            col = th.color("accent") if k == 1 else th.color("ink")
            vl = fx.text_layer(txt, fx.fit_font(th, "numbers", txt, 120, self.W * 0.4), col)
            ll = fx.text_layer(label, fx.fit_font(th, "display", label, 52, self.W * 0.4), th.color("muted"))
            dx, dy = (off, 0) if not th.vertical else (0, off)
            fx.paste(f, vl, (cx + dx, cy + dy), opacity=p, anchor="mm")
            fx.paste(f, ll, (cx + dx, cy + dy + vl.height / 2 + th.s(20)), opacity=p, anchor="mt")
        pv = fx.ease_out_back(fx.progress(lt, self.beat(0) + 0.2, 0.4))
        if pv > 0:
            r = th.s(52)
            disc = Image.new("RGBA", (r * 2, r * 2), (0, 0, 0, 0))
            ImageDraw.Draw(disc).ellipse((0, 0, r * 2 - 1, r * 2 - 1), fill=th.color("accent"))
            disc.alpha_composite(self.vs, (r - self.vs.width // 2, r - self.vs.height // 2))
            fx.paste(f, disc, (self.W / 2, self.H * (0.5 if not th.vertical else 0.40)), scale=max(0.01, pv), anchor="mm")
        return f


class Kinetic(Graphic):
    def __init__(self, *a):
        super().__init__(*a)
        th = self.th
        words = [i.label for i in self.g.items][:5] or self.sc.text.split()[:4]
        self.words = words
        self.layers = []
        for k, w in enumerate(words):
            t = w.upper() if th.upper("display") or k % 2 == 0 else w
            role = "display" if k % 2 == 0 else "accent"
            size = 150 if k % 2 == 0 else 92
            f = fx.fit_font(th, role, t, size if not th.vertical else size * 0.9, self.W * 0.84)
            self.layers.append((fx.text_layer(t, f, th.color("ink")), fx.text_layer(t, f, th.color("accent"))))

    def beat(self, i: int) -> float:
        if len(self.sc.beat_times) > i:
            return self.sc.beat_times[i] - self.sc.start
        n = max(1, len(self.words))
        return 0.15 + (self.dur * 0.75) * i / n

    def frame(self, lt: float) -> Image.Image:
        th = self.th
        f = self.base.copy()
        n = len(self.layers)
        total = sum(l[0].height for l in self.layers) * 0.92
        y = self.H * (0.5 if not th.vertical else 0.4) - total / 2
        cur = max([k for k in range(n) if lt >= self.beat(k)] or [-1])
        drift = th.s(14) * math.sin(lt * 0.8)
        for k, (ink, acc) in enumerate(self.layers):
            tb = self.beat(k)
            if lt >= tb:
                p = fx.ease_out(fx.progress(lt, tb, 0.32))
                s = 1.25 - 0.25 * p
                lay = acc if k == cur else ink
                fx.paste(f, lay, (self.W / 2 + (drift if k % 2 else -drift), y + ink.height / 2), opacity=p,
                         scale=s, anchor="mm")
            y += ink.height * 0.92
        return f


GRAPHICS = {
    "big_number": BigNumber, "bar_chart": BarChart, "line_chart": LineChart, "timeline": Timeline,
    "list": ListGraphic, "quote": Quote, "compare": Compare, "kinetic": Kinetic,
}


def make(sc: Scene, plan: Plan, th: Theme, media: MediaCache) -> Renderer:
    if sc.kind in ("graphic", "kinetic") and sc.graphic is not None:
        return GRAPHICS.get(sc.graphic.type, Kinetic)(sc, plan, th, media)
    if sc.clip_path and sc.kind in ("html", "title"):
        return HtmlClip(sc, plan, th, media)
    if sc.kind == "title":
        return TitleCard(sc, plan, th, media)
    if sc.kind == "chapter":
        return Chapter(sc, plan, th, media)
    if sc.kind == "outro":
        return Outro(sc, plan, th, media)
    a = media.asset(sc.asset_id)
    if a is not None and a.kind == "video":
        return FootageVideo(sc, plan, th, media)
    if a is not None and sc.motion.frame == "card":
        return FootageCard(sc, plan, th, media)
    return FootageImage(sc, plan, th, media)


__all__ = ["make", "MediaCache", "GRAPHICS"]
