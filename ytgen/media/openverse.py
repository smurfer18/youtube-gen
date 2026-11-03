"""Openverse: openly licensed images aggregated from many collections."""

from __future__ import annotations

from ytgen.config import Settings
from ytgen.media import license_ok, register
from ytgen.models import Asset
from ytgen.util import net

API = "https://api.openverse.org/v1/images/"


@register("openverse")
class Openverse:
    name = "openverse"

    def __init__(self, settings: Settings):
        self.settings = settings

    def enabled(self) -> bool:
        return True

    def search(self, query: str, vertical: bool, limit: int = 12) -> list[Asset]:
        d = net.get_json(API, timeout=12, params={
            "q": query, "license_type": "commercial,modification", "page_size": limit,
            "aspect_ratio": "tall" if vertical else "wide", "mature": "false",
        })
        out = []
        for r in d.get("results", []):
            lic = f"CC {r.get('license', '').upper()} {r.get('license_version', '')}".strip()
            if r.get("license") in ("cc0", "pdm"):
                lic = "CC0" if r["license"] == "cc0" else "Public domain"
            if not license_ok(lic):
                continue
            out.append(Asset(
                id=f"ov-{r['id']}", url=r["url"], provider=f"Openverse / {r.get('provider', '')}",
                title=(r.get("title") or "")[:120], author=r.get("creator") or "Unknown", license=lic,
                license_url=r.get("license_url", ""), page_url=r.get("foreign_landing_url", ""),
                width=r.get("width") or 0, height=r.get("height") or 0, thumb_url=r.get("thumbnail") or "",
                query=f"{r.get('title','')} {' '.join(t.get('name','') for t in r.get('tags') or [])}",
            ))
        return out
