"""Pexels and Pixabay stock photos and video clips (free API keys:
PEXELS_API_KEY, PIXABAY_API_KEY). Video clips become moving b-roll scenes."""

from __future__ import annotations

from ytgen.config import Settings
from ytgen.media import register
from ytgen.models import Asset
from ytgen.util import net


@register("pexels")
class Pexels:
    name = "pexels"

    def __init__(self, settings: Settings):
        self.key = settings.pexels_api_key
        self.video = True

    def enabled(self) -> bool:
        return bool(self.key)

    def search(self, query: str, vertical: bool, limit: int = 12) -> list[Asset]:
        h = {"Authorization": self.key}
        orient = "portrait" if vertical else "landscape"
        out: list[Asset] = []
        if self.video:
            d = net.get_json("https://api.pexels.com/videos/search", headers=h, timeout=20,
                             params={"query": query, "orientation": orient, "per_page": max(4, limit // 3)})
            for v in d.get("videos", []):
                files = sorted((f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4"),
                               key=lambda f: abs((f.get("width") or 0) - (1080 if vertical else 1920)))
                if not files:
                    continue
                f = files[0]
                out.append(Asset(
                    id=f"pxv-{v['id']}", kind="video", url=f["link"], provider="Pexels",
                    title=v.get("url", "").rstrip("/").rsplit("/", 1)[-1].replace("-", " "),
                    author=(v.get("user") or {}).get("name", "Pexels"), license="Pexels License",
                    license_url="https://www.pexels.com/license/", page_url=v.get("url", ""),
                    width=f.get("width") or 0, height=f.get("height") or 0, duration=v.get("duration") or 0,
                    query=v.get("url", "").replace("-", " "), thumb_url=v.get("image", ""),
                ))
        d = net.get_json("https://api.pexels.com/v1/search", headers=h, timeout=20,
                         params={"query": query, "orientation": orient, "per_page": limit})
        for p in d.get("photos", []):
            out.append(Asset(
                id=f"px-{p['id']}", url=p["src"].get("large2x") or p["src"]["original"], provider="Pexels",
                title=p.get("alt") or "", author=p.get("photographer", "Pexels"), license="Pexels License",
                license_url="https://www.pexels.com/license/", page_url=p.get("url", ""),
                width=p.get("width") or 0, height=p.get("height") or 0, query=p.get("alt") or "",
                thumb_url=p["src"].get("medium", ""),
            ))
        return out


@register("pixabay")
class Pixabay:
    name = "pixabay"

    def __init__(self, settings: Settings):
        self.key = settings.pixabay_api_key

    def enabled(self) -> bool:
        return bool(self.key)

    def search(self, query: str, vertical: bool, limit: int = 12) -> list[Asset]:
        d = net.get_json("https://pixabay.com/api/", timeout=20, params={
            "key": self.key, "q": query[:100], "image_type": "photo", "per_page": max(3, limit),
            "orientation": "vertical" if vertical else "horizontal", "safesearch": "true",
        })
        return [Asset(
            id=f"pb-{h['id']}", url=h["largeImageURL"], provider="Pixabay", title=h.get("tags", ""),
            author=h.get("user", "Pixabay"), license="Pixabay Content License",
            license_url="https://pixabay.com/service/license-summary/", page_url=h.get("pageURL", ""),
            width=h.get("imageWidth") or 0, height=h.get("imageHeight") or 0, query=h.get("tags", ""),
            thumb_url=h.get("webformatURL", ""),
        ) for h in d.get("hits", [])]
