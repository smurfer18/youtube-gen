"""Editing presets: pacing, transitions mix, graphics density, title sequence,
maps, letterbox, camera drift, sound levels. One YAML file per preset; add
your own next to these or pass a path."""

from __future__ import annotations

from pathlib import Path

import yaml

DIR = Path(__file__).resolve().parent


def available() -> list[str]:
    return sorted(p.stem for p in DIR.glob("*.yaml"))


def load(name: str) -> dict:
    p = DIR / f"{name}.yaml"
    if not p.exists() and Path(name).exists():
        p = Path(name)
    if not p.exists():
        raise ValueError(f"unknown preset {name!r}; available: {available()}")
    return yaml.safe_load(p.read_text())
