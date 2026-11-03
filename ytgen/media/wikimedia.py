"""Wikimedia Commons: millions of freely licensed historical and documentary images."""

from __future__ import annotations

from ytgen.config import Settings
from ytgen.media import license_ok, register, strip_html
from ytgen.models import Asset
from ytgen.util import log, net

L = log.get("media")
API = "https://commons.wikimedia.org/w/api.php"


@register("wikimedia")
class Wikimedia:
    name = "wikimedia"

    def __init__(self, settings: Settings):
        self.width = 1920 if settings.format == "long" else 1280  # standard thumbnail steps

    def enabled(self) -> bool:
        return True

    def search(self, query: str, vertical: bool, limit: int = 12) -> list[Asset]:
        net.pace("commons.wikimedia.org", 0.35)
        d = net.get_json(API, timeout=20, params={
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
            "gsrsearch": f"filetype:bitmap {query}", "gsrlimit": limit,
            "prop": "imageinfo", "iiprop": "url|size|extmetadata|mime", "iiurlwidth": self.width,
            "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|ImageDescription|ObjectName",
        })
        pages = sorted(d.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
        out = []
        for p in pages:
            ii = (p.get("imageinfo") or [{}])[0]
            if ii.get("mime") not in ("image/jpeg", "image/png", "image/webp", "image/tiff"):
                continue
            m = ii.get("extmetadata", {})
            lic = m.get("LicenseShortName", {}).get("value", "")
            if not license_ok(lic):
                continue
            title = p["title"].removeprefix("File:").rsplit(".", 1)[0]
            desc = strip_html(m.get("ImageDescription", {}).get("value", ""))[:300]
            out.append(Asset(
                id=f"wm-{p['pageid']}", url=ii.get("thumburl") or ii["url"], provider="Wikimedia Commons",
                title=title, author=strip_html(m.get("Artist", {}).get("value", ""))[:120] or "Unknown",
                license=lic, license_url=m.get("LicenseUrl", {}).get("value", ""),
                page_url=ii.get("descriptionurl", ""), width=ii.get("width", 0), height=ii.get("height", 0),
                query=f"{title} {desc}",
            ))
        return out
