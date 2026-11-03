"""Font resolution: Google Fonts (OFL, downloaded once to the cache) with
fontconfig fallbacks, so themes can name families freely and still render
offline."""

from __future__ import annotations

import functools
import os
import re
import subprocess
from pathlib import Path

from PIL import ImageFont

from ytgen.util import log, net

L = log.get("fonts")
CACHE = Path(os.environ.get("YTGEN_CACHE", Path.home() / ".cache" / "ytgen")) / "fonts"

FALLBACKS = {
    "serif": ["DejaVu Serif:bold", "Caladea:bold", "FreeSerif:bold"],
    "sans": ["Inter Display:bold", "Inter:bold", "DejaVu Sans:bold", "FreeSans:bold"],
    "mono": ["JetBrains Mono:bold", "DejaVu Sans Mono:bold"],
}
SERIF_HINT = re.compile(r"serif|playfair|lora|garamond|baskerville|merriweather|caslon|bodoni|didot|georgia", re.I)


def _google(family: str, weight: int, italic: bool) -> Path | None:
    name = f"{family.replace(' ', '')}-{weight}{'i' if italic else ''}.ttf"
    dest = CACHE / name
    if dest.exists():
        return dest
    axis = f"ital,wght@{1 if italic else 0},{weight}" if italic else f"wght@{weight}"
    try:
        # no browser User-Agent -> Google serves TrueType (Pillow-friendly), not WOFF2
        r = net.session().get("https://fonts.googleapis.com/css2",
                              params={"family": f"{family}:{axis}"}, timeout=15,
                              headers={"User-Agent": "ytgen-fonts"})
        if r.status_code != 200 and weight != 400:
            r = net.session().get("https://fonts.googleapis.com/css2", params={"family": family},
                                  timeout=15, headers={"User-Agent": "ytgen-fonts"})
        r.raise_for_status()
        m = re.search(r"url\((https://fonts\.gstatic\.com/[^)]+\.ttf)\)", r.text)
        if not m:
            return None
        net.download(m.group(1), dest, timeout=30)
        return dest
    except Exception as e:
        L.debug("google font %s %s unavailable: %s", family, weight, e)
        return None


def _fc(pattern: str) -> Path | None:
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True, text=True, timeout=5).stdout
        return Path(out) if out and Path(out).exists() else None
    except Exception:
        return None


@functools.lru_cache(maxsize=None)
def font_path(family: str, weight: int = 700, italic: bool = False) -> str:
    local = _fc(f"{family}:weight={_fc_weight(weight)}{':slant=100' if italic else ''}")
    if local and _family_matches(local, family):
        return str(local)
    g = _google(family, weight, italic)
    if g:
        return str(g)
    kind = "mono" if "mono" in family.lower() else ("serif" if SERIF_HINT.search(family) else "sans")
    for pat in FALLBACKS[kind]:
        p = _fc(pat)
        if p:
            L.warning("font %r unavailable, using %s", family, p.name)
            return str(p)
    return str(_fc("sans:bold") or "")


def _fc_weight(w: int) -> int:
    return {100: 0, 200: 40, 300: 50, 400: 80, 500: 100, 600: 180, 700: 200, 800: 205, 900: 210}.get(w, 200)


def _family_matches(path: Path, family: str) -> bool:
    try:
        fam = ImageFont.truetype(str(path), 12).getname()[0]
    except Exception:
        return False
    return fam.replace(" ", "").lower().startswith(family.replace(" ", "").lower()[:6])


@functools.lru_cache(maxsize=512)
def font(path: str, size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(path, max(6, int(size)))
    try:  # variable fonts: dial in the weight
        axes = f.get_variation_axes()
        vals = []
        for ax in axes:
            nm = ax.get("name", b"")
            nm = nm.decode() if isinstance(nm, bytes) else str(nm)
            if nm.lower().startswith("weight") or nm == "wght":
                vals.append(max(ax["minimum"], min(ax["maximum"], weight)))
            else:
                vals.append(ax.get("default", ax["minimum"]))
        if vals:
            f.set_variation_by_axes(vals)
    except Exception:
        pass
    return f
