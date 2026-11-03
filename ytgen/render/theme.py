"""Theme loading and helpers (colors, fonts, scale)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml
from PIL import ImageColor, ImageFont

from ytgen.render import fonts

THEME_DIR = Path(__file__).resolve().parent.parent / "themes"

_AUTO = [
    ("orbit", r"space|nasa|apollo|moon|mars|planet|galaxy|star|rocket|astronaut|universe|cosmos|telescope|satellite|ai\b|robot|future|technology|science"),
    ("ledger", r"money|finance|econom|stock|invest|bank|billion|market|company|business|crypto|wealth|tax|real estate|price"),
    ("noir", r"crime|murder|mystery|mysteries|killer|heist|spy|espionage|disappear|cold war|conspiracy|disaster|unsolved|trial|prison"),
    ("archive", r"history|ancient|empire|war|medieval|king|queen|roman|egypt|century|dynasty|revolution|biography|battle|civilization"),
    ("atlas", r"explain|how|why|geograph|country|countries|city|travel|map|ocean|nature|animal|island"),
]


def available() -> list[str]:
    return sorted(p.stem for p in THEME_DIR.glob("*.yaml"))


def pick_auto(topic: str, vertical: bool) -> str:
    t = topic.lower()
    for name, pat in _AUTO:
        if re.search(pat, t):
            return name
    return "pulse" if vertical else "noir"


def load(name: str, extra_dirs: Optional[list[Path]] = None) -> dict:
    for d in [*(extra_dirs or []), THEME_DIR]:
        p = Path(d) / f"{name}.yaml"
        if p.exists():
            return yaml.safe_load(p.read_text())
    p = Path(name)
    if p.exists():
        return yaml.safe_load(p.read_text())
    raise ValueError(f"unknown theme {name!r}; available: {available()}")


def rgba(c: str | tuple, alpha: Optional[int] = None) -> tuple[int, int, int, int]:
    if isinstance(c, tuple):
        t = tuple(c) + ((255,) if len(c) == 3 else ())
    else:
        c = c.strip()
        if re.fullmatch(r"#[0-9a-fA-F]{8}", c):
            t = tuple(int(c[i:i + 2], 16) for i in (1, 3, 5, 7))
        else:
            t = ImageColor.getrgb(c) + (255,)
            t = t[:4]
    if alpha is not None:
        t = t[:3] + (alpha,)
    return t  # type: ignore[return-value]


class Theme:
    def __init__(self, data: dict, width: int, height: int):
        self.d = data
        self.name = data["name"]
        self.W, self.H = width, height
        self.u = min(width, height) / 1080.0  # layout unit: 1.0 at 1080p
        self.vertical = height > width
        self.pal = data["palette"]

    def color(self, name: str, alpha: Optional[int] = None) -> tuple[int, int, int, int]:
        v = self.pal.get(name, name)
        if name == "highlight":
            v = self.d["captions"].get("highlight", "accent")
            v = self.pal.get(v, v)
        return rgba(v, alpha)

    def rgb(self, name: str) -> tuple[int, int, int]:
        return self.color(name)[:3]

    def font(self, role: str, size: float) -> ImageFont.FreeTypeFont:
        spec = self.d["fonts"][role]
        path = fonts.font_path(spec["family"], int(spec.get("weight", 700)), bool(spec.get("italic", False)))
        return fonts.font(path, int(size * self.u), int(spec.get("weight", 700)))

    def upper(self, role: str) -> bool:
        return bool(self.d["fonts"][role].get("upper", False))

    def s(self, v: float) -> int:
        """Scale a 1080p pixel value to the current frame."""
        return int(round(v * self.u))

    @property
    def captions(self) -> dict:
        return self.d["captions"]

    @property
    def transition(self) -> dict:
        return self.d.get("transitions", {"default": "crossfade", "duration": 0.4})


__all__ = ["Theme", "load", "available", "pick_auto", "rgba", "fonts"]
