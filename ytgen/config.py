"""Settings: defaults < config file (ytgen.yaml) < environment < CLI flags."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field


class Settings(BaseModel):
    # what to make
    format: Literal["long", "short", "square"] = "long"
    resolution: int = 1080               # short side in pixels: 720, 1080, 1440, 2160 (4K)
    preset_name: str = "documentary"     # editing preset (see `ytgen presets`)
    target_minutes: float = 8.0          # long-form target; shorts aim for ~50 s
    language: str = "en"
    theme: str = "auto"                  # theme name or "auto" (picked from the topic)
    caption_style: Optional[str] = None  # override the theme's caption style
    channel_name: str = "Your Channel"

    # providers
    writer: str = "auto"                 # auto | claude | extractive | file
    voice: str = "edge:en-US-AndrewMultilingualNeural"
    voice_rate: str = "+0%"
    media_sources: list[str] = Field(
        default_factory=lambda: ["wikimedia", "nasa", "archive", "pexels", "pixabay", "unsplash", "smithsonian"]
    )
    semantic: bool = True                # CLIP re-ranking of candidate shots
    allow_uncertain_licenses: bool = False
    ai_images: str = "pollinations"      # off | pollinations (free) | openai | replicate (paid, budget-gated)
    ai_budget_usd: float = 0.0           # hard cap for paid generation per project
    research: bool = True
    fact_check: bool = True

    # Claude
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "high"

    # render
    fps: int = 30
    workers: int = Field(default_factory=lambda: max(1, (os.cpu_count() or 2)))
    crf: int = 17
    encoder_preset: str = "veryfast"     # x264 speed/size trade-off
    music: bool = True
    music_db: float = -19.0
    sfx: bool = True
    sfx_db: float = -14.0
    loudness_lufs: float = -14.0
    subscribe_banner: bool = True
    thumbnails: int = 3

    # keys (read from the environment, never written to disk)
    anthropic_api_key: Optional[str] = Field(default=None, exclude=True)
    pexels_api_key: Optional[str] = Field(default=None, exclude=True)
    pixabay_api_key: Optional[str] = Field(default=None, exclude=True)
    elevenlabs_api_key: Optional[str] = Field(default=None, exclude=True)

    @property
    def width(self) -> int:
        r = self.resolution
        return _even(r * 16 / 9) if self.format == "long" else r

    @property
    def height(self) -> int:
        r = self.resolution
        return _even(r * 16 / 9) if self.format == "short" else r

    @property
    def vertical(self) -> bool:
        return self.format == "short"

    @property
    def square(self) -> bool:
        return self.format == "square"


def _even(x: float) -> int:
    return int(round(x / 2)) * 2


ENV_KEYS = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "pexels_api_key": "PEXELS_API_KEY",
    "pixabay_api_key": "PIXABAY_API_KEY",
    "elevenlabs_api_key": "ELEVENLABS_API_KEY",
}


def load_settings(config_path: Optional[str | Path] = None, **overrides) -> Settings:
    data: dict = {}
    candidates = [Path(config_path)] if config_path else [Path("ytgen.yaml"), Path.home() / ".config/ytgen/ytgen.yaml"]
    for p in candidates:
        if p and p.is_file():
            data.update(yaml.safe_load(p.read_text()) or {})
            break
    for field, env in ENV_KEYS.items():
        if os.environ.get(env):
            data[field] = os.environ[env]
    data.update({k: v for k, v in overrides.items() if v is not None})
    return Settings(**data)
