"""Internet Archive: public-domain and openly licensed films and images
(Prelinger, NASA, government films, ...). Only items whose `licenseurl`
declares public domain or a commercial-reuse Creative Commons license are
returned; everything else on the Archive is skipped as uncertain."""

from __future__ import annotations

import re

from ytgen.config import Settings
from ytgen.media import register
from ytgen.media.license import classify
from ytgen.models import Asset
from ytgen.util import net

SEARCH = "https://archive.org/advancedsearch.php"


def _lic_name(url: str) -> str:
    u = url.lower()
    if "publicdomain/zero" in u:
        return "CC0"
    if "publicdomain" in u:
        return "Public domain"
    m = re.search(r"licenses/([a-z-]+)/([\d.]+)", u)
    return f"CC {m.group(1).upper()} {m.group(2)}" if m else url


@register("archive")
class InternetArchive:
    name = "archive"

    def __init__(self, settings: Settings):
        self.settings = settings

    def enabled(self) -> bool:
        return True

    def search(self, query: str, vertical: bool, limit: int = 12) -> list[Asset]:
        net.pace("archive.org", 0.5)
        q = f"({query}) AND (mediatype:movies OR mediatype:image) AND licenseurl:*"
        d = net.get_json(SEARCH, timeout=20, params={
            "q": q, "fl[]": ["identifier", "title", "licenseurl", "mediatype", "creator", "description"],
            "rows": limit, "output": "json", "sort[]": "downloads desc"})
        out = []
        for doc in d.get("response", {}).get("docs", []):
            lic = _lic_name(doc.get("licenseurl", ""))
            status, _, ok = classify(lic)
            if not ok or status in ("uncertain", "rejected"):
                continue
            ident = doc["identifier"]
            if ident.lower().startswith(("youtube-", "yt-")) or "youtube" in str(doc.get("title", "")).lower():
                continue  # re-uploads: the declared license cannot be verified
            creator = doc.get("creator") or "Internet Archive"
            creator = ", ".join(creator) if isinstance(creator, list) else creator
            desc = doc.get("description") or ""
            desc = " ".join(desc) if isinstance(desc, list) else desc
            out.append(Asset(
                id=f"ia-{ident}"[:80], kind="video" if doc.get("mediatype") == "movies" else "image",
                url=f"ia:{ident}", provider="Internet Archive", title=str(doc.get("title", ident))[:140],
                author=str(creator)[:120], license=lic, license_url=doc.get("licenseurl", ""),
                page_url=f"https://archive.org/details/{ident}", thumb_url=f"https://archive.org/services/img/{ident}",
                query=f"{doc.get('title', '')} {re.sub('<[^>]+>', ' ', desc)[:300]}",
            ))
        return out


def resolve(asset: Asset, max_mb: int = 150) -> str:
    """Pick a downloadable file for an `ia:` asset (smallest decent MP4 / largest JPEG)."""
    ident = asset.url.split(":", 1)[1]
    md = net.get_json(f"https://archive.org/metadata/{ident}", timeout=30)
    files = md.get("files", [])
    if asset.kind == "video":
        vids = [f for f in files if f.get("name", "").lower().endswith(".mp4")
                and int(f.get("size", 0) or 0) < max_mb * 1e6]
        vids.sort(key=lambda f: (-(int(f.get("height", 0) or 0) >= 360), int(f.get("size", 0) or 0)))
        if not vids:
            raise RuntimeError("no suitable mp4")
        asset.duration = float(vids[0].get("length", 0) or md.get("metadata", {}).get("runtime", 0) or 0) if \
            str(vids[0].get("length", "0")).replace(".", "").isdigit() else 0.0
        name = vids[0]["name"]
    else:
        imgs = [f for f in files if f.get("name", "").lower().endswith((".jpg", ".jpeg", ".png"))
                and "thumb" not in f.get("name", "").lower()]
        imgs.sort(key=lambda f: -int(f.get("size", 0) or 0))
        if not imgs:
            raise RuntimeError("no image file")
        name = imgs[0]["name"]
    return f"https://archive.org/download/{ident}/{name}"
