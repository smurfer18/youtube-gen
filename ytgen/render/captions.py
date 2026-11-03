"""Word-synced burned-in captions.

Styles
  boxed    documentary: phrase in a rounded plate, the spoken word in the highlight colour
  karaoke  no plate, stroked text; upcoming words dimmed, the current word highlighted
  pop      Shorts: 1-3 big uppercase words that pop in, current word highlighted
  minimal  plain text with a soft shadow
"""

from __future__ import annotations

import bisect
from functools import lru_cache

from PIL import Image

from ytgen.models import CaptionPhrase, Plan
from ytgen.render import fx
from ytgen.render.theme import Theme, rgba


class Captions:
    def __init__(self, plan: Plan, theme: Theme):
        self.theme = theme
        self.style = plan.caption_style
        self.phrases = plan.captions
        self.starts = [p.start for p in self.phrases]
        c = theme.captions
        self.vertical = theme.vertical
        size = c.get("size_short" if self.vertical else "size_long", 52)
        role = "display" if self.style == "pop" else "body"
        self.upper = self.style == "pop"
        self.font = theme.font(role, size)
        self.ink = rgba(c.get("ink", "#FFFFFF"))
        self.hi = theme.color("highlight")
        self.alt = theme.color("accent2") if "accent2" in theme.pal else self.hi
        self.box = rgba(c.get("box", "#000000CC"))
        self.max_w = theme.W * (0.86 if self.vertical else 0.64)
        self.stroke = theme.s(7 if self.style == "pop" else 3) if self.style in ("pop", "karaoke") else 0
        self._layout = lru_cache(maxsize=16)(self._layout_impl)

    # ------------------------------------------------------------ layout --
    def _word_layers(self, text: str):
        t = text.upper() if self.upper else text
        sh = self.theme.s(10) if self.style in ("pop", "karaoke", "minimal") else 0
        mk = lambda fill: fx.text_layer(t, self.font, fill, stroke=self.stroke, stroke_fill=(0, 0, 0, 235),
                                        shadow_blur=sh, shadow_opacity=0.55)
        return (mk(self.ink), mk(self.hi), mk(self.ink[:3] + (150,)), mk(self.alt)), sh * 2

    def _layout_impl(self, idx: int):
        ph: CaptionPhrase = self.phrases[idx]
        space = self.font.getlength(" ")
        built = [self._word_layers(w.text) for w in ph.words]
        layers = [b[0] for b in built]
        pad = built[0][1] if built else 0
        ink_w = [lay[0].width - pad * 2 for lay in layers]
        lines: list[list[int]] = [[]]
        width = 0.0
        for i in range(len(layers)):
            w = ink_w[i]
            if lines[-1] and width + space + w > self.max_w:
                lines.append([])
                width = 0
            width += (space if lines[-1] else 0) + w
            lines[-1].append(i)
        if len(lines) > 2 and not self.vertical:  # rebalance into two lines
            half = len(ph.words) // 2
            lines = [list(range(half)), list(range(half, len(ph.words)))]
        lh = (layers[0][0].height - pad * 2) * (0.92 if self.style == "pop" else 1.0)
        pos = {}
        widths = []
        for li, line in enumerate(lines):
            widths.append(sum(ink_w[i] for i in line) + space * (len(line) - 1))
        bw = max(widths)
        for li, line in enumerate(lines):
            x = (bw - widths[li]) / 2
            for i in line:
                pos[i] = (x - pad, li * lh - pad)
                x += ink_w[i] + space
        bh = lh * len(lines)
        return layers, pos, int(bw), int(bh), pad

    # ------------------------------------------------------------ render --
    def active(self, t: float) -> int:
        i = bisect.bisect_right(self.starts, t) - 1
        if i >= 0 and t < self.phrases[i].end:
            return i
        return -1

    def draw(self, frame: Image.Image, t: float) -> None:
        idx = self.active(t)
        if idx < 0:
            return
        ph = self.phrases[idx]
        layers, pos, bw, bh, lpad = self._layout(idx)
        th = self.theme
        pad_x, pad_y = th.s(26) + lpad, th.s(14) + lpad
        block = Image.new("RGBA", (bw + pad_x * 2, bh + pad_y * 2), (0, 0, 0, 0))
        if self.style == "boxed":
            box = fx.rounded((bw + th.s(52), int(bh + th.s(28))), th.s(14), self.box)
            block.alpha_composite(box, (lpad, lpad))
        for i, w in enumerate(ph.words):
            base, hi, dim, alt = layers[i]
            spoken = t >= w.start
            current = w.start <= t < max(w.end, w.start + 0.12)
            if self.style == "minimal":
                lay = base
            elif current:
                lay = hi
            elif w.emphasis and spoken:
                lay = alt if self.style == "pop" else hi
            elif self.style == "karaoke" and not spoken:
                lay = dim
            else:
                lay = base
            x, y = pos[i]
            if current and self.style in ("pop", "karaoke"):
                s = 1.0 + 0.08 * (1 - fx.ease_out(fx.progress(t, w.start, 0.14)))
                cx, cy = x + base.width / 2, y + base.height / 2
                fx.paste(block, lay, (pad_x + cx, pad_y + cy), scale=s, anchor="mm")
            else:
                block.alpha_composite(lay, (int(pad_x + x), int(pad_y + y)))
        # entrance animation
        age = t - ph.start
        if self.style == "pop":
            s = 0.82 + 0.18 * fx.ease_out_back(fx.progress(age, 0, 0.18))
            op = fx.progress(age, 0, 0.06)
        else:
            s = 1.0
            op = fx.ease_out(fx.progress(age, 0, 0.12))
        rise = (1 - fx.ease_out(fx.progress(age, 0, 0.18))) * th.s(10)
        if self.vertical:
            cx, cy = th.W / 2, th.H * 0.71
        else:
            cx, cy = th.W / 2, th.H - th.s(118) - bh / 2
        fx.paste(frame, block, (cx, cy + rise), opacity=op, scale=s, anchor="mm")
