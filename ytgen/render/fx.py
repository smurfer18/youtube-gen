"""Low-level visual building blocks: easing, grading, textures, backgrounds,
text layers, shadows. Everything returns Pillow images; heavy work is cached."""

from __future__ import annotations

import functools
import random

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from ytgen.render.theme import Theme, rgba

# ---------------------------------------------------------------- easing ----


def clamp(x: float, a: float = 0.0, b: float = 1.0) -> float:
    return a if x < a else b if x > b else x


def ease_out(t: float) -> float:
    t = clamp(t)
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    t = clamp(t)
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_out_back(t: float, s: float = 1.6) -> float:
    t = clamp(t)
    return 1 + (s + 1) * (t - 1) ** 3 + s * (t - 1) ** 2


def progress(t: float, start: float, dur: float) -> float:
    return clamp((t - start) / dur) if dur > 0 else float(t >= start)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# --------------------------------------------------------------- grading ----


def grade(img: Image.Image, g: dict) -> Image.Image:
    img = img.convert("RGB")
    sat = g.get("saturation", 1.0)
    if sat != 1.0:
        img = ImageEnhance.Color(img).enhance(sat)
    if g.get("contrast", 1.0) != 1.0:
        img = ImageEnhance.Contrast(img).enhance(g["contrast"])
    if g.get("brightness", 1.0) != 1.0:
        img = ImageEnhance.Brightness(img).enhance(g["brightness"])
    amt = g.get("tint_amount", 0.0)
    if amt > 0:
        tint = rgba(g.get("tint", "#000000"))[:3]
        toned = ImageOps.colorize(img.convert("L"), black=(8, 6, 4), white=(255, 250, 240), mid=tint)
        img = Image.blend(img, toned, amt)
    return img


@functools.lru_cache(maxsize=8)
def vignette(w: int, h: int, strength: float) -> Image.Image:
    """RGB multiply layer: white centre falling off to dark corners."""
    y, x = np.ogrid[-1:1:complex(0, h), -1:1:complex(0, w)]
    r = np.sqrt((x * 0.9) ** 2 + (y * 1.0) ** 2)
    m = 1 - strength * np.clip((r - 0.45) / 0.95, 0, 1) ** 1.6
    a = (np.clip(m, 0, 1) * 255).astype(np.uint8)
    return Image.fromarray(a, "L").convert("RGB")


@functools.lru_cache(maxsize=4)
def _grain_tiles(w: int, h: int, amount: float, n: int = 6) -> list[np.ndarray]:
    rng = np.random.default_rng(7)
    tiles = []
    for _ in range(n):
        g = rng.normal(0, 255 * amount, (h // 2, w // 2)).astype(np.float32)
        g = np.repeat(np.repeat(g, 2, axis=0), 2, axis=1)[:h, :w]
        tiles.append(g.astype(np.int16)[:, :, None])
    return tiles


def post(frame: Image.Image, theme: Theme, fi: int) -> Image.Image:
    tx = theme.d.get("texture", {})
    v = tx.get("vignette", 0)
    if v > 0:
        frame = ImageChops.multiply(frame, vignette(frame.width, frame.height, v))
    g = tx.get("grain", 0)
    if g > 0:
        arr = np.asarray(frame, dtype=np.int16)
        tile = _grain_tiles(frame.width, frame.height, g)[fi % 6]
        arr = np.clip(arr + tile, 0, 255).astype(np.uint8)
        frame = Image.fromarray(arr, "RGB")
    return frame


# ----------------------------------------------------------- backgrounds ----


_BG_CACHE: dict[tuple, Image.Image] = {}


def make_background(theme: Theme) -> Image.Image:
    key = (theme.name, theme.W, theme.H)
    if key in _BG_CACHE:
        return _BG_CACHE[key]
    W, H = theme.W, theme.H
    b = theme.d.get("background", {})
    kind = b.get("kind", "gradient")
    bg = theme.rgb("bg")
    img = Image.new("RGB", (W, H), bg)
    glow = rgba(b.get("glow", theme.pal["surface"]))[:3]
    # soft radial glow, off-centre
    y, x = np.ogrid[0:H, 0:W]
    cx, cy = W * 0.62, H * 0.42
    d = np.sqrt(((x - cx) / (W * 0.7)) ** 2 + ((y - cy) / (H * 0.8)) ** 2)
    m = np.clip(1 - d, 0, 1) ** 1.8
    arr = np.asarray(img, dtype=np.float32)
    arr = arr * (1 - m[..., None]) + np.array(glow, np.float32) * m[..., None]
    rng = np.random.default_rng(3)
    if kind == "paper":
        fib = rng.normal(0, 1, (H // 4, W // 4)).astype(np.float32)
        fib = np.asarray(Image.fromarray(((fib * 18) + 128).clip(0, 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
                         .filter(ImageFilter.GaussianBlur(1.2)), dtype=np.float32) - 128
        streak = np.asarray(Image.fromarray((rng.normal(128, 30, (H // 2, 6)).clip(0, 255)).astype(np.uint8))
                            .resize((W, H), Image.BICUBIC), dtype=np.float32) - 128
        arr = arr + fib[..., None] * 0.35 + streak[..., None] * 0.12
    img = Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGB")
    dr = ImageDraw.Draw(img, "RGBA")
    if kind == "dots":
        sp = max(8, theme.s(b.get("spacing", 46)))
        r = max(1, theme.s(b.get("dot", 1.6)))
        a = int(255 * b.get("opacity", 0.08))
        for yy in range(sp // 2, H, sp):
            for xx in range(sp // 2, W, sp):
                dr.ellipse((xx - r, yy - r, xx + r, yy + r), fill=(255, 255, 255, a))
    elif kind == "grid":
        sp = max(8, theme.s(b.get("spacing", 64)))
        a = int(255 * b.get("opacity", 0.07))
        acc = theme.rgb("accent")
        for xx in range(0, W, sp):
            dr.line((xx, 0, xx, H), fill=acc + (a,), width=1)
        for yy in range(0, H, sp):
            dr.line((0, yy, W, yy), fill=acc + (a,), width=1)
    elif kind == "stars":
        rr = random.Random(11)
        for _ in range(int(W * H / 2600)):
            xx, yy = rr.random() * W, rr.random() * H
            s = rr.random() ** 3 * 2.2 * theme.u + 0.4
            a = int(60 + rr.random() ** 2 * 195)
            dr.ellipse((xx - s, yy - s, xx + s, yy + s), fill=(220, 235, 255, a))
    _BG_CACHE[key] = img
    return img


# ---------------------------------------------------------------- shapes ----


def rounded(size: tuple[int, int], radius: int, fill) -> Image.Image:
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=fill)
    return im


def shadow(layer: Image.Image, blur: int, opacity: float, spread: int = 0) -> Image.Image:
    """Soft shadow for an RGBA layer (returned larger by `blur*2` on each side)."""
    pad = blur * 2 + spread
    a = layer.getchannel("A")
    canvas = Image.new("L", (layer.width + pad * 2, layer.height + pad * 2), 0)
    canvas.paste(a, (pad, pad))
    if spread:
        canvas = canvas.filter(ImageFilter.MaxFilter(spread * 2 + 1))
    canvas = canvas.filter(ImageFilter.GaussianBlur(blur))
    canvas = canvas.point(lambda v: int(v * opacity))
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sh.putalpha(canvas)
    return sh


def text_layer(text: str, font: ImageFont.FreeTypeFont, fill, stroke: int = 0, stroke_fill=(0, 0, 0, 255),
               tracking: float = 0.0, shadow_blur: int = 0, shadow_opacity: float = 0.6) -> Image.Image:
    """Tight RGBA layer for a single line of text (optional letter tracking)."""
    if tracking:
        widths = [font.getlength(ch) for ch in text]
        extra = font.size * tracking
        w = int(sum(widths) + extra * max(0, len(text) - 1)) + stroke * 2 + 4
    else:
        w = int(font.getlength(text)) + stroke * 2 + 4
    asc, desc = font.getmetrics()
    h = asc + desc + stroke * 2 + 4
    im = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if tracking:
        x = stroke + 2
        for ch, cw in zip(text, widths):
            d.text((x, stroke + 2), ch, font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
            x += cw + font.size * tracking
    else:
        d.text((stroke + 2, stroke + 2), text, font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    if shadow_blur:
        sh = shadow(im, shadow_blur, shadow_opacity)
        pad = shadow_blur * 2
        out = Image.new("RGBA", sh.size, (0, 0, 0, 0))
        out.alpha_composite(sh, (0, shadow_blur // 2))
        out.alpha_composite(im, (pad, pad))
        return out
    return im


def wrap(text: str, font: ImageFont.FreeTypeFont, max_w: float, max_lines: int = 3) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if font.getlength(trial) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(",.;:") + "…"
    return lines


def fit_font(theme: Theme, role: str, text: str, size: float, max_w: float, min_size: float = 20) -> ImageFont.FreeTypeFont:
    f = theme.font(role, size)
    while f.getlength(text) > max_w and size > min_size:
        size *= 0.92
        f = theme.font(role, size)
    return f


def with_opacity(layer: Image.Image, a: float) -> Image.Image:
    if a >= 0.999:
        return layer
    out = layer.copy()
    out.putalpha(out.getchannel("A").point(lambda v: int(v * clamp(a))))
    return out


def paste(canvas: Image.Image, layer: Image.Image, xy: tuple[float, float], opacity: float = 1.0,
          scale: float = 1.0, anchor: str = "lt") -> None:
    """Alpha-composite `layer` onto RGB/RGBA canvas at xy with opacity/scale.
    anchor: lt (left-top), mm (centre), lm (left-middle), mt (middle-top)."""
    if opacity <= 0.003:
        return
    if abs(scale - 1.0) > 0.003:
        layer = layer.resize((max(1, int(layer.width * scale)), max(1, int(layer.height * scale))), Image.BILINEAR)
    x, y = xy
    if anchor[0] == "m":
        x -= layer.width / 2
    if anchor[1] == "m":
        y -= layer.height / 2
    if anchor[1] == "b":
        y -= layer.height
    if anchor[0] == "r":
        x -= layer.width
    layer = with_opacity(layer, opacity)
    x, y = int(round(x)), int(round(y))
    if canvas.mode == "RGBA":
        canvas.alpha_composite(layer, (max(0, x), max(0, y)), (max(0, -x), max(0, -y)))
    else:
        canvas.paste(layer, (x, y), layer)


def cover(img: Image.Image, w: int, h: int, overscan: float = 1.0) -> Image.Image:
    """Resize to cover (w*overscan, h*overscan) keeping aspect."""
    tw, th = int(w * overscan), int(h * overscan)
    s = max(tw / img.width, th / img.height)
    return img.resize((max(tw, int(img.width * s + 0.5)), max(th, int(img.height * s + 0.5))), Image.LANCZOS)


def blur_backdrop(img: Image.Image, w: int, h: int, radius: int, dim: float) -> Image.Image:
    small = cover(img, w // 4, h // 4)
    small = small.crop(((small.width - w // 4) // 2, (small.height - h // 4) // 2,
                        (small.width - w // 4) // 2 + w // 4, (small.height - h // 4) // 2 + h // 4))
    small = small.filter(ImageFilter.GaussianBlur(max(1, radius // 4)))
    big = small.resize((w, h), Image.BICUBIC)
    return ImageEnhance.Brightness(big).enhance(dim)

