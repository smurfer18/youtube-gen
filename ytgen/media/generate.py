"""AI image generation as a last resort when no licensed footage matches.

Providers
  pollinations  free, no key (default fallback). Output is marked ai_generated
                and flagged in the manifest for review.
  openai        OPENAI_API_KEY, gpt-image-1    ~$0.04-0.17 / image   (paid)
  replicate     REPLICATE_API_TOKEN, flux-schnell ~$0.003 / image   (paid)

Paid providers only run within ``--ai-budget`` (USD, default 0 = never). Every
call is written to the project's cost ledger (costs.json) before and after,
so spend is auditable and capped.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
from pathlib import Path
from typing import Optional

from ytgen.models import Asset
from ytgen.util import log, net

L = log.get("generate")
PRICES = {"pollinations": 0.0, "openai": 0.17, "replicate": 0.003}  # conservative per-image estimates


class Ledger:
    def __init__(self, project_dir: Path, budget: float):
        self.path = project_dir / "costs.json"
        self.budget = budget
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {"entries": [], "total_usd": 0.0}
        self.blocked: set[str] = set()

    @property
    def spent(self) -> float:
        return float(self.data["total_usd"])

    def allow(self, cost: float) -> bool:
        return cost == 0 or self.spent + cost <= self.budget + 1e-9

    def add(self, provider: str, what: str, cost: float) -> None:
        self.data["entries"].append({"t": time.time(), "provider": provider, "item": what, "usd": cost})
        self.data["total_usd"] = round(self.spent + cost, 4)
        self.path.write_text(json.dumps(self.data, indent=1))


def style_prompt(query: str, theme: dict, vertical: bool) -> str:
    sat = theme.get("grade", {}).get("saturation", 1.0)
    look = ("black and white archival documentary photograph, film grain" if sat < 0.3
            else "documentary photograph, natural light, realistic, high detail")
    return f"{query}, {look}, no text, no watermark, {'vertical 9:16' if vertical else 'wide 16:9'} composition"


def generate(query: str, provider: str, dest_dir: Path, ledger: Ledger, theme: dict, W: int, H: int,
             seed: int = 0) -> Optional[Asset]:
    cost = PRICES.get(provider, 1.0)
    if provider in ledger.blocked:
        return None
    if not ledger.allow(cost):
        L.info("AI image skipped (budget %.2f USD reached)", ledger.budget)
        return None
    prompt = style_prompt(query, theme, H > W)
    dest = dest_dir / f"ai_{provider}_{abs(hash((prompt, seed))) % 10**10}.jpg"
    try:
        if provider == "pollinations":
            w, h = (1280, 720) if W >= H else (720, 1280)
            url = (f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}"
                   f"?width={w}&height={h}&nologo=true&seed={seed}")  # default (free) model only
            net.download(url, dest, timeout=180)
            lic = "AI-generated (Pollinations.ai)"
        elif provider == "openai":
            key = os.environ["OPENAI_API_KEY"]
            r = net.session().post("https://api.openai.com/v1/images/generations", timeout=300,
                                   headers={"Authorization": f"Bearer {key}"},
                                   json={"model": "gpt-image-1", "prompt": prompt, "size": "1536x1024" if W >= H else "1024x1536"})
            r.raise_for_status()
            import base64
            dest.write_bytes(base64.b64decode(r.json()["data"][0]["b64_json"]))
            lic = "AI-generated (OpenAI gpt-image-1)"
        elif provider == "replicate":
            tok = os.environ["REPLICATE_API_TOKEN"]
            r = net.session().post("https://api.replicate.com/v1/models/black-forest-labs/flux-schnell/predictions",
                                   timeout=300, headers={"Authorization": f"Bearer {tok}", "Prefer": "wait"},
                                   json={"input": {"prompt": prompt, "aspect_ratio": "16:9" if W >= H else "9:16",
                                                   "output_format": "jpg"}})
            r.raise_for_status()
            out = r.json()["output"]
            net.download(out[0] if isinstance(out, list) else out, dest, timeout=120)
            lic = "AI-generated (FLUX.1 schnell via Replicate)"
        else:
            raise ValueError(provider)
    except Exception as e:
        if "402" in str(e):  # the service wants payment: never pay implicitly, stop asking this run
            ledger.blocked.add(provider)
            L.warning("AI generation (%s) now requires payment; disabled for this run", provider)
        else:
            L.warning("AI generation (%s) failed: %s", provider, e)
        return None
    ledger.add(provider, prompt[:120], cost)
    return Asset(id=dest.stem, path=str(dest), provider=f"AI ({provider})", title=query, author="AI-generated",
                 license=lic, license_status="ai_generated", attribution_required=False, commercial_ok=True,
                 generated=True, cost_usd=cost, query=query)
