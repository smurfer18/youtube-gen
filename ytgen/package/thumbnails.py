"""Thumbnail generator: three A/B variants at 1280x720 (or 1080x1920 cover
frames for Shorts) built from the video's most striking shots, the theme's
type and the script's thumbnail text."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from ytgen.models import Plan, Script
from ytgen.render import fx
from ytgen.render.theme import Theme
from ytgen.util import log

L = log.get("thumbs")


def punch(path: str) -> float:
    """Colourfulness + sharpness + contrast: a proxy for thumbnail impact."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((256, 256))
        a = np.asarray(im, dtype=np.float32)
    rg, yb = a[..., 0] - a[..., 1], 0.5 * (a[..., 0] + a[..., 1]) - a[..., 2]
    colorful = np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    g = a.mean(-1)
    sharp = np.abs(np.diff(g, axis=0)).mean() + np.abs(np.diff(g, axis=1)).mean()
    return float(colorful * 0.6 + sharp * 2.0 + g.std() * 0.5)


def _headline(text: str) -> tuple[list[str], int]:
    words = text.upper().split()
    if len(words) <= 2:
        lines = [" ".join(words)]
    else:
        mid = (len(words) + 1) // 2
        lines = [" ".join(words[:mid]), " ".join(words[mid:])]
    # accent the number, or the last word
    flat = " ".join(lines).split()
    acc = next((i for i, w in enumerate(flat) if re.search(r"\d", w)), len(flat) - 1)
    return lines, acc


def _draw_headline(canvas: Image.Image, th: Theme, text: str, box: tuple[int, int, int, int], align: str = "left") -> None:
    x0, y0, x1, y1 = box
    lines, acc = _headline(text)
    size = 150
    while size > 40:
        f = th.font("display", size)
        if all(f.getlength(l) <= (x1 - x0) for l in lines) and len(lines) * size * 1.05 <= (y1 - y0):
            break
        size -= 6
    f = th.font("display", size)
    lh = int(size * th.u * 1.04)
    y = y0 + ((y1 - y0) - lh * len(lines)) // 2
    k = 0
    stroke = max(4, int(size * th.u * 0.06))
    for line in lines:
        words = line.split()
        lw = f.getlength(line)
        x = x0 if align == "left" else x0 + ((x1 - x0) - lw) / 2
        for w in words:
            col = th.color("accent") if k == acc else (255, 255, 255, 255)
            lay = fx.text_layer(w, f, col, stroke=stroke, stroke_fill=(0, 0, 0, 255), shadow_blur=int(14 * th.u), shadow_opacity=0.7)
            pad = int(14 * th.u) * 2
            canvas.paste(lay, (int(x - pad), int(y - pad)), lay)
            x += f.getlength(w + " ")
            k += 1
        y += lh


def make_thumbnails(plan: Plan, script: Script, theme_data: dict, out_dir: Path, count: int = 3) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    vertical = plan.height > plan.width
    W, H = (1080, 1920) if vertical else (1280, 720)
    th = Theme(theme_data, W, H)
    used = {sc.asset_id for sc in plan.scenes if sc.asset_id and sc.kind in ("footage", "chapter")}
    imgs = [plan.assets[a] for a in used if a in plan.assets and plan.assets[a].kind == "image" and plan.assets[a].path]
    ranked = sorted(imgs, key=lambda a: -punch(a.path))[: max(count, 1)]
    text = script.thumbnail_text or " ".join(script.title.split()[:4])
    outs: list[Path] = []
    for k in range(count):
        a = ranked[k % len(ranked)] if ranked else None
        if a is not None:
            with Image.open(a.path) as im:
                base = ImageOps.exif_transpose(im).convert("RGB")
            base = fx.cover(base, W, H)
            base = base.crop(((base.width - W) // 2, (base.height - H) // 2, (base.width - W) // 2 + W, (base.height - H) // 2 + H))
            base = ImageEnhance.Color(base).enhance(1.25)
            base = ImageEnhance.Contrast(base).enhance(1.15)
        else:
            base = fx.make_background(th).copy()
        canvas = base.convert("RGBA")
        variant = k % 3
        if vertical:
            grad = Image.linear_gradient("L").resize((W, H))
            shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            shade.putalpha(grad.point(lambda v: int(max(0, v - 90) * 1.3)))
            canvas.alpha_composite(shade)
            _draw_headline(canvas, th, text, (int(W * 0.08), int(H * 0.58), int(W * 0.92), int(H * 0.86)), "center")
        elif variant == 0:   # text left over a dark gradient
            grad = Image.linear_gradient("L").rotate(90, expand=True).resize((W, H))
            shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            shade.putalpha(grad.point(lambda v: int(max(0, v - 70) * 1.25)))
            canvas.alpha_composite(shade)
            ImageDraw.Draw(canvas).rectangle((48, 70, 60, H - 70), fill=th.color("accent"))
            _draw_headline(canvas, th, text, (88, 60, int(W * 0.62), H - 60))
        elif variant == 1:   # split panel
            panel = Image.new("RGBA", (int(W * 0.46), H), th.color("bg", 245))
            mask = Image.new("L", panel.size, 255)
            ImageDraw.Draw(mask).polygon([(panel.width - 90, 0), (panel.width, 0), (panel.width, H), (panel.width - 10, H)], fill=0)
            canvas.paste(panel, (0, 0), mask)
            ImageDraw.Draw(canvas).line((int(W * 0.46) - 90, 0, int(W * 0.46) - 10, H), fill=th.color("accent"), width=10)
            _draw_headline(canvas, th, text, (56, 60, int(W * 0.40), H - 60))
        else:                # centred, punched-in, ringed focus
            z = canvas.resize((int(W * 1.3), int(H * 1.3)), Image.LANCZOS)
            canvas = z.crop(((z.width - W) // 2, (z.height - H) // 2, (z.width - W) // 2 + W, (z.height - H) // 2 + H))
            canvas = Image.alpha_composite(canvas, Image.new("RGBA", (W, H), (0, 0, 0, 80)))
            canvas = Image.fromarray(np.asarray(canvas.convert("RGB"))).convert("RGBA")
            v = fx.vignette(W, H, 0.7).convert("RGBA")
            canvas = Image.fromarray((np.asarray(canvas, dtype=np.float32) * (np.asarray(v, dtype=np.float32) / 255)).astype(np.uint8))
            ring = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(ring).ellipse((W * 0.62, H * 0.12, W * 0.94, H * 0.62), outline=th.color("accent"), width=12)
            canvas.alpha_composite(ring.filter(ImageFilter.GaussianBlur(0.6)))
            _draw_headline(canvas, th, text, (60, int(H * 0.52), int(W * 0.9), H - 40))
        p = out_dir / f"thumbnail_{'ABC'[k % 3]}.jpg"
        canvas.convert("RGB").save(p, quality=92, optimize=True)
        outs.append(p)
    L.info("thumbnails: %s", ", ".join(p.name for p in outs))
    return outs
