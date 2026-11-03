"""Persistent overlays: lower thirds (place/date name cards), the subscribe
banner, and the chapter progress bar."""

from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageDraw

from ytgen.models import Plan
from ytgen.render import fx
from ytgen.render.theme import Theme


class Overlays:
    def __init__(self, plan: Plan, theme: Theme):
        self.plan = plan
        self.th = theme
        self.lt = [(sc.start + sc.lower_third_at, sc.lower_third) for sc in plan.scenes if sc.lower_third]
        self._lt_layer = lru_cache(maxsize=8)(self._lower_third_layer)
        self._banner = None

    # ------------------------------------------------------- lower third --
    def _lower_third_layer(self, text: str) -> Image.Image:
        th = self.th
        parts = [p.strip() for p in text.replace("|", "·").split("·")]
        title = parts[0]
        sub = " · ".join(parts[1:])
        f1 = th.font("display", 46)
        f2 = th.font("accent", 34)
        t1 = fx.text_layer(title.upper() if th.upper("display") else title, f1, th.color("ink"), tracking=0.02)
        t2 = fx.text_layer(sub, f2, th.color("muted")) if sub else None
        bar = th.s(8)
        pad = th.s(22)
        w = max(t1.width, t2.width if t2 else 0) + pad * 2 + bar
        h = t1.height + (t2.height if t2 else 0) + pad * 1.3
        layer = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
        layer.alpha_composite(fx.rounded(layer.size, th.s(6), th.color("surface", 225)))
        ImageDraw.Draw(layer).rectangle((0, 0, bar, layer.height), fill=th.color("accent"))
        layer.alpha_composite(t1, (bar + pad, int(pad * 0.55)))
        if t2:
            layer.alpha_composite(t2, (bar + pad, int(pad * 0.55 + t1.height * 0.98)))
        return layer

    def _draw_lower_third(self, frame: Image.Image, t: float) -> None:
        for t0, text in self.lt:
            age = t - t0
            if 0 <= age < 4.6:
                layer = self._lt_layer(text)
                inn = fx.ease_out(fx.progress(age, 0, 0.45))
                out = 1 - fx.ease_in_out(fx.progress(age, 4.1, 0.5))
                x = self.th.s(96) - (1 - inn) * self.th.s(40)
                y = self.th.s(70)
                if self.th.vertical:
                    x = (self.th.W - layer.width) / 2
                    y = self.th.H * 0.18
                fx.paste(frame, layer, (x, y), opacity=min(inn, out))

    # ------------------------------------------------------------ banner --
    def _banner_layer(self) -> Image.Image:
        th = self.th
        name = self.plan.channel_name or "Subscribe"
        f1 = th.font("body", 30)
        f2 = th.font("body", 26)
        initials = "".join(w[0] for w in name.split()[:2]).upper() or "Y"
        av = th.s(68)
        t1 = fx.text_layer(name, f1, th.color("ink"))
        t2 = fx.text_layer("New documentaries every week", f2, th.color("muted"))
        btn_txt = fx.text_layer("SUBSCRIBE", th.font("body", 26), (255, 255, 255, 255), tracking=0.06)
        btn = fx.rounded((btn_txt.width + th.s(40), btn_txt.height + th.s(18)), th.s(8), (204, 0, 0, 255))
        btn.alpha_composite(btn_txt, (th.s(20), th.s(9)))
        pad = th.s(20)
        w = pad * 3 + av + max(t1.width, t2.width) + pad + btn.width
        h = av + pad * 2
        layer = fx.rounded((int(w), int(h)), th.s(18), th.color("surface", 235))
        d = ImageDraw.Draw(layer)
        d.ellipse((pad, pad, pad + av, pad + av), fill=th.color("accent"))
        il = fx.text_layer(initials, th.font("display", 30), th.color("on_accent"))
        layer.alpha_composite(il, (int(pad + av / 2 - il.width / 2), int(pad + av / 2 - il.height / 2)))
        layer.alpha_composite(t1, (pad * 2 + av, int(pad + av / 2 - t1.height + th.s(4))))
        layer.alpha_composite(t2, (pad * 2 + av, int(pad + av / 2 + th.s(2))))
        layer.alpha_composite(btn, (int(w - pad - btn.width), int(h / 2 - btn.height / 2)))
        return layer

    def _draw_banner(self, frame: Image.Image, t: float) -> None:
        t0 = self.plan.banner_at
        if t0 is None or not (t0 <= t < t0 + 6.2):
            return
        if self._banner is None:
            self._banner = self._banner_layer()
        age = t - t0
        inn = fx.ease_out(fx.progress(age, 0, 0.45))
        out = 1 - fx.ease_in_out(fx.progress(age, 5.6, 0.5))
        th = self.th
        x = th.W - self._banner.width - th.s(64)
        y = th.s(60) - (1 - min(inn, out)) * th.s(30)
        fx.paste(frame, self._banner, (x, y), opacity=min(inn, out))

    # ------------------------------------------------------- progress bar --
    def _draw_progress(self, frame: Image.Image, t: float) -> None:
        if self.th.vertical or len(self.plan.chapters) < 3 or self.plan.extra.get("progress_bar") is False:
            return
        th = self.th
        d = ImageDraw.Draw(frame, "RGBA")
        h = max(3, th.s(5))
        dur = self.plan.duration
        gap = th.s(6)
        marks = [c.start for c in self.plan.chapters] + [dur]
        for a, b in zip(marks, marks[1:]):
            x0 = a / dur * th.W + (gap / 2 if a > 0 else 0)
            x1 = b / dur * th.W - gap / 2
            d.rectangle((x0, 0, x1, h), fill=(255, 255, 255, 46))
            xf = min(x1, t / dur * th.W)
            if xf > x0:
                d.rectangle((x0, 0, xf, h), fill=th.color("accent", 230))

    def _draw_ai_badge(self, frame: Image.Image, t: float) -> None:
        """Transparency: any AI-generated image on screen is labelled as such."""
        sc = next((s for s in self.plan.scenes if s.start <= t < s.end), None)
        a = self.plan.assets.get(sc.asset_id) if sc and sc.asset_id else None
        if not (a and a.generated and sc.kind in ("footage", "chapter", "title", "outro")):
            return
        if getattr(self, "_ai", None) is None:
            th = self.th
            txt = fx.text_layer("AI ILLUSTRATION", th.font("body", 20), (255, 255, 255, 230), tracking=0.12)
            self._ai = fx.rounded((txt.width + th.s(24), txt.height + th.s(10)), th.s(6), (0, 0, 0, 150))
            self._ai.alpha_composite(txt, (th.s(12), th.s(5)))
        fx.paste(frame, self._ai, (self.th.W - self._ai.width - self.th.s(40), self.th.H - self._ai.height - self.th.s(40)))

    def draw(self, frame: Image.Image, t: float) -> None:
        self._draw_ai_badge(frame, t)
        self._draw_progress(frame, t)
        self._draw_lower_third(frame, t)
        self._draw_banner(frame, t)
