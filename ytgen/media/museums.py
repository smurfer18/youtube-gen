"""Key-gated high-quality libraries: Unsplash (UNSPLASH_ACCESS_KEY) and the
Smithsonian Open Access API (SMITHSONIAN_API_KEY from api.data.gov; CC0 items
only)."""

from __future__ import annotations

import os

from ytgen.config import Settings
from ytgen.media import register
from ytgen.models import Asset
from ytgen.util import net


@register("unsplash")
class Unsplash:
    name = "unsplash"

    def __init__(self, settings: Settings):
        self.key = os.environ.get("UNSPLASH_ACCESS_KEY")

    def enabled(self) -> bool:
        return bool(self.key)

    def search(self, query: str, vertical: bool, limit: int = 12) -> list[Asset]:
        d = net.get_json("https://api.unsplash.com/search/photos", timeout=20,
                         headers={"Authorization": f"Client-ID {self.key}", "Accept-Version": "v1"},
                         params={"query": query, "per_page": limit, "orientation": "portrait" if vertical else "landscape"})
        return [Asset(
            id=f"us-{p['id']}", url=p["urls"]["full"] + "&w=2400", provider="Unsplash",
            title=p.get("alt_description") or p.get("description") or "", author=p["user"]["name"],
            license="Unsplash License", license_url="https://unsplash.com/license", page_url=p["links"]["html"],
            width=p.get("width", 0), height=p.get("height", 0), thumb_url=p["urls"]["small"],
            query=" ".join(t.get("title", "") for t in p.get("tags", [])) + " " + (p.get("alt_description") or ""),
        ) for p in d.get("results", [])]


@register("smithsonian")
class Smithsonian:
    name = "smithsonian"

    def __init__(self, settings: Settings):
        self.key = os.environ.get("SMITHSONIAN_API_KEY")

    def enabled(self) -> bool:
        return bool(self.key)

    def search(self, query: str, vertical: bool, limit: int = 12) -> list[Asset]:
        d = net.get_json("https://api.si.edu/openaccess/api/v1.0/search", timeout=20,
                         params={"q": f"{query} AND online_media_type:Images", "rows": limit, "api_key": self.key})
        out = []
        for r in d.get("response", {}).get("rows", []):
            c = r.get("content", {})
            media = (c.get("descriptiveNonRepeating", {}).get("online_media", {}) or {}).get("media", [])
            m = next((m for m in media if m.get("usage", {}).get("access") == "CC0" and m.get("type") == "Images"), None)
            if not m:
                continue
            res = m.get("resources") or []
            big = next((x for x in res if "High" in x.get("label", "") or "Screen" in x.get("label", "")), None)
            out.append(Asset(
                id=f"si-{r['id']}"[:80], url=(big or m)["url"] if big else m.get("content", ""), provider="Smithsonian Open Access",
                title=r.get("title", ""), author=c.get("descriptiveNonRepeating", {}).get("data_source", "Smithsonian"),
                license="CC0", license_url="https://creativecommons.org/publicdomain/zero/1.0/",
                page_url=c.get("descriptiveNonRepeating", {}).get("record_link", ""), thumb_url=m.get("thumbnail", ""),
                query=r.get("title", ""),
            ))
        return out
