"""Script writers. A writer turns a topic (plus optional research) into a Script.

Register new writers with ``@register("name")``; the pipeline picks one by name
(``--writer``) or automatically: Claude when an API key is available, else the
offline extractive writer.
"""

from __future__ import annotations

from typing import Callable, Protocol

from ytgen.config import Settings
from ytgen.models import Script
from ytgen.research import Research


class Writer(Protocol):
    def write(self, topic: str, research: Research, settings: Settings) -> Script: ...


_REGISTRY: dict[str, Callable[[], Writer]] = {}


def register(name: str):
    def deco(factory):
        _REGISTRY[name] = factory
        return factory
    return deco


def get_writer(name: str, settings: Settings) -> Writer:
    from ytgen.writers import claude, extractive  # noqa: F401  (register)

    if name == "auto":
        name = "claude" if claude.available(settings) else "extractive"
    if name not in _REGISTRY:
        raise ValueError(f"unknown writer {name!r}; available: {sorted(_REGISTRY)}")
    return _REGISTRY[name]()


def available() -> list[str]:
    from ytgen.writers import claude, extractive  # noqa: F401

    return sorted(_REGISTRY)
