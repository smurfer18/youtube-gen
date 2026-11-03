"""NASA Image and Video Library (public domain; NASA media usage guidelines apply)."""

from __future__ import annotations

import re

from ytgen.config import Settings
from ytgen.media import register
from ytgen.models import Asset
from ytgen.util import net

API = "https://images-api.nasa.gov/search"
SPACE = re.compile(
    r"\b(nasa|apollo|gemini|mercury|artemis|shuttle|space|orbit|rocket|launch|moon|lunar|mars|jupiter|saturn|"
    r"venus|planet|galaxy|nebula|star|astronaut|cosmonaut|iss|hubble|webb|telescope|earth from|asteroid|comet|"
    r"solar|satellite|spacecraft|mission control|capsule)\b", re.I)


@register("nasa")
class Nasa:
    name = "nasa"

    def __init__(self, settings: Settings):
        self.settings = settings

    def enabled(self) -> bool:
        return True

    def search(self, query: str, vertical: bool, limit: int = 12) -> list[Asset]:
        if not SPACE.search(query):
            return []  # only useful (and only queried) for space / aviation topics
        d = net.get_json(API, params={"q": query, "media_type": "image", "page_size": limit}, timeout=20)
        out = []
        for it in d.get("collection", {}).get("items", [])[:limit]:
            data = (it.get("data") or [{}])[0]
            nid = data.get("nasa_id")
            if not nid:
                continue
            out.append(Asset(
                id=f"nasa-{nid}", url=f"https://images-assets.nasa.gov/image/{nid}/{nid}~large.jpg",
                provider="NASA", title=data.get("title", nid)[:120],
                author=data.get("photographer") or data.get("secondary_creator") or data.get("center") or "NASA",
                license="Public domain (NASA)", license_url="https://www.nasa.gov/nasa-brand-center/images-and-media/",
                page_url=f"https://images.nasa.gov/details/{nid}", query=f"{data.get('title','')} {data.get('description','')[:300]}",
            ))
        return out
