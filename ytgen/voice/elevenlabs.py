"""ElevenLabs voices via the REST ``with-timestamps`` endpoint (character alignment
converted to word timings). Requires ELEVENLABS_API_KEY."""

from __future__ import annotations

import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ytgen.config import Settings
from ytgen.util import log, net
from ytgen.voice import Clip, register

L = log.get("voice")
API = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps"


def chars_to_words(chars: list[str], starts: list[float], ends: list[float]) -> list[tuple[float, float, str]]:
    words, cur, s0, e0 = [], "", None, None
    for c, s, e in zip(chars, starts, ends):
        if c.isspace():
            if cur:
                words.append((s0, e0, cur))
            cur, s0 = "", None
            continue
        if s0 is None:
            s0 = s
        cur += c
        e0 = e
    if cur:
        words.append((s0, e0, cur))
    return words


@register("elevenlabs")
class ElevenLabsVoice:
    def __init__(self, voice_id: str, settings: Settings):
        if not settings.elevenlabs_api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is not set")
        self.voice_id = voice_id or "JBFqnCBsd6RMkjVDRZzb"
        self.key = settings.elevenlabs_api_key
        self.model = "eleven_multilingual_v2"

    def _one(self, text: str, dest: Path) -> Clip:
        meta = dest.with_suffix(".json")
        if dest.exists() and meta.exists():
            return Clip(dest, [tuple(w) for w in json.loads(meta.read_text())])
        r = net.session().post(
            API.format(voice=self.voice_id),
            headers={"xi-api-key": self.key, "Content-Type": "application/json"},
            json={"text": text, "model_id": self.model, "output_format": "mp3_44100_128"},
            timeout=120,
        )
        r.raise_for_status()
        d = r.json()
        dest.write_bytes(base64.b64decode(d["audio_base64"]))
        al = d.get("normalized_alignment") or d["alignment"]
        words = chars_to_words(al["characters"], al["character_start_times_seconds"], al["character_end_times_seconds"])
        meta.write_text(json.dumps(words))
        return Clip(dest, words)

    def synth_many(self, texts: list[str], out_dir: Path) -> list[Clip]:
        out_dir.mkdir(parents=True, exist_ok=True)
        jobs = []
        for i, t in enumerate(texts):
            h = hashlib.sha1(f"{self.voice_id}|{self.model}|{t}".encode()).hexdigest()[:10]
            jobs.append((t, out_dir / f"{i:04d}_{h}.mp3"))
        L.info("synthesizing %d beats with ElevenLabs %s", len(texts), self.voice_id)
        with ThreadPoolExecutor(3) as ex:
            return list(ex.map(lambda j: self._one(*j), jobs))
