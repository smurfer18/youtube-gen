"""Your own media folder (``--media local:/path/to/folder``): images and clips
matched to scenes by filename keywords. You vouch for the rights."""

from __future__ import annotations

import re
from pathlib import Path

from ytgen.config import Settings
from ytgen.media import register
from ytgen.models import Asset
from ytgen.util.text import norm_key

IMG = {".jpg", ".jpeg", ".png", ".webp"}
VID = {".mp4", ".mov", ".mkv", ".webm"}


@register("local")
class LocalFolder:
    name = "local"

    def __init__(self, settings: Settings, folder: str = "media"):
        self.root = Path(folder).expanduser()

    def enabled(self) -> bool:
        return self.root.is_dir()

    def search(self, query: str, vertical: bool, limit: int = 12) -> list[Asset]:
        q = {norm_key(w) for w in re.findall(r"\w+", query) if len(w) > 2}
        hits = []
        for p in self.root.rglob("*"):
            ext = p.suffix.lower()
            if ext not in IMG | VID:
                continue
            name = {norm_key(w) for w in re.findall(r"[a-zA-Z0-9]+", p.stem)}
            score = len(q & name)
            if score:
                hits.append((score, p, ext))
        hits.sort(key=lambda h: -h[0])
        return [Asset(
            id=f"local-{norm_key(str(p))[-24:]}", kind="video" if ext in VID else "image", path=str(p),
            provider="Local media", title=p.stem, author="Owner", license="Owned / licensed by creator",
            query=p.stem.replace("_", " ").replace("-", " "),
        ) for _, p, ext in hits[:limit]]
