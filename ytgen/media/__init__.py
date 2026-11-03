"""Licensed media sources.

A source turns a query into candidate ``Asset``s carrying their provenance
(provider, author, license, page URL). Only licenses that allow commercial use
and modification are returned, so every shot can be used in a monetized video
and is credited automatically.

Register new sources (AI image/video generators, private libraries, ...) with
``@register("name")``.
"""

from __future__ import annotations

import re
from typing import Callable, Protocol

from ytgen.config import Settings
from ytgen.models import Asset

_REGISTRY: dict[str, Callable[[Settings], "MediaSource"]] = {}


class MediaSource(Protocol):
    name: str

    def enabled(self) -> bool: ...

    def search(self, query: str, vertical: bool, limit: int = 12) -> list[Asset]: ...


def register(name: str):
    def deco(factory):
        _REGISTRY[name] = factory
        return factory
    return deco


def sources(settings: Settings) -> list[MediaSource]:
    from ytgen.media import local, nasa, openverse, stock, wikimedia  # noqa: F401

    out = []
    for name in settings.media_sources:
        f = _REGISTRY.get(name.split(":")[0])
        if f is None:
            raise ValueError(f"unknown media source {name!r}; available: {sorted(_REGISTRY)}")
        src = f(settings) if ":" not in name else f(settings, name.split(":", 1)[1])  # type: ignore[call-arg]
        if src.enabled():
            out.append(src)
    return out


_OK = re.compile(r"^(cc0|pd|public domain|cc[- ]by(?:[- ]sa)?(?:[- ][\d.]+)?|attribution|pexels|pixabay|nasa|no restrictions|copyrighted free use)", re.I)
_BAD = re.compile(r"\b(nc|nd|noncommercial|no ?derivs|fair use|non-free|all rights reserved)\b", re.I)


def license_ok(lic: str) -> bool:
    lic = (lic or "").strip()
    return bool(lic) and bool(_OK.search(lic)) and not _BAD.search(lic)


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    return re.sub(r"\s+", " ", s).strip()
