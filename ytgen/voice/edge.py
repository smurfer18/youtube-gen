"""Microsoft Edge neural voices (free, no key) with WordBoundary timings."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from ytgen.config import Settings
from ytgen.util import log, net
from ytgen.voice import Clip, register

L = log.get("voice")


@register("edge")
class EdgeVoice:
    def __init__(self, voice: str, settings: Settings):
        self.voice = voice or "en-US-AndrewMultilingualNeural"
        self.rate = settings.voice_rate

    async def _one(self, text: str, dest: Path, sem: asyncio.Semaphore) -> Clip:
        meta = dest.with_suffix(".json")
        if dest.exists() and meta.exists():
            return Clip(dest, [tuple(w) for w in json.loads(meta.read_text())])
        import edge_tts
        import edge_tts.communicate as ec

        ec._SSL_CTX = net.ssl_context()  # honour SSL_CERT_FILE (proxies / custom CAs)
        async with sem:
            for attempt in range(4):
                words: list[tuple[float, float, str]] = []
                try:
                    comm = edge_tts.Communicate(text, self.voice, rate=self.rate, boundary="WordBoundary")
                    with open(dest, "wb") as f:
                        async for ch in comm.stream():
                            if ch["type"] == "audio":
                                f.write(ch["data"])
                            elif ch["type"] == "WordBoundary":
                                s = ch["offset"] / 1e7
                                words.append((round(s, 4), round(s + ch["duration"] / 1e7, 4), ch["text"]))
                    break
                except Exception as e:  # transient websocket errors
                    if attempt == 3:
                        raise
                    L.warning("edge-tts retry %d: %s", attempt + 1, e)
                    await asyncio.sleep(1.5 * (attempt + 1))
        meta.write_text(json.dumps(words))
        return Clip(dest, words)

    def synth_many(self, texts: list[str], out_dir: Path) -> list[Clip]:
        out_dir.mkdir(parents=True, exist_ok=True)

        async def run():
            sem = asyncio.Semaphore(4)
            tasks = []
            for i, t in enumerate(texts):
                h = hashlib.sha1(f"{self.voice}|{self.rate}|{t}".encode()).hexdigest()[:10]
                tasks.append(self._one(t, out_dir / f"{i:04d}_{h}.mp3", sem))
            return await asyncio.gather(*tasks)

        L.info("synthesizing %d beats with %s", len(texts), self.voice)
        return asyncio.run(run())
