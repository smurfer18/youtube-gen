"""Narration synthesis with word-level timing.

Every voice provider returns, per beat, an audio file plus word boundaries
(seconds, relative to the clip). ``narrate`` stitches the beats with natural
pauses into one track and produces the global word clock (``Timing``) that
drives cuts, captions, graphic beats and SFX.

Voice spec strings: ``edge:<VoiceName>`` (default, free neural voices),
``elevenlabs:<voice_id>`` (ELEVENLABS_API_KEY), ``file:<path>`` (your own
recorded narration, aligned with faster-whisper).
"""

from __future__ import annotations

import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

from ytgen.config import Settings
from ytgen.models import Script, Sentence, Timing, Word
from ytgen.util import log
from ytgen.util.text import norm_key, split_sentences

L = log.get("voice")
SR = 48000


@dataclass
class Clip:
    path: Path
    words: list[tuple[float, float, str]]  # (start, end, text) relative to the clip


class Voice(Protocol):
    def synth_many(self, texts: list[str], out_dir: Path) -> list[Clip]: ...


_REGISTRY: dict[str, Callable[[str, Settings], Voice]] = {}


def register(name: str):
    def deco(factory):
        _REGISTRY[name] = factory
        return factory
    return deco


def get_voice(spec: str, settings: Settings) -> Voice:
    from ytgen.voice import edge, elevenlabs, recorded  # noqa: F401

    kind, _, arg = spec.partition(":")
    if kind not in _REGISTRY:
        raise ValueError(f"unknown voice provider {kind!r}; available: {sorted(_REGISTRY)}")
    return _REGISTRY[kind](arg, settings)


def decode_pcm(path: Path) -> np.ndarray:
    """Any audio file -> float32 mono at 48 kHz."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def write_wav(path: Path, pcm: np.ndarray, sr: int = SR, channels: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(pcm, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((data * 32767).astype("<i2").tobytes())


def _trim(pcm: np.ndarray, words: list[tuple[float, float, str]], pad: float = 0.06) -> tuple[np.ndarray, float]:
    """Trim leading/trailing silence to the spoken span (keeps a little air)."""
    if not words:
        return pcm, 0.0
    a = max(0.0, words[0][0] - pad)
    b = min(len(pcm) / SR, words[-1][1] + 0.25)
    return pcm[int(a * SR):int(b * SR)], a


def narrate(script: Script, settings: Settings, out_dir: Path, title_gap: float = 0.0) -> Timing:
    out_dir.mkdir(parents=True, exist_ok=True)
    voice = get_voice(settings.voice, settings)
    beats = [(ci, b) for ci, c in enumerate(script.chapters) for b in c.beats]
    clips = voice.synth_many([b.text for _, b in beats], out_dir / "clips")

    short = settings.format == "short"
    lead_in = 0.15 if short else 0.35
    beat_gap = 0.12 if short else 0.32
    chapter_gap = 0.0 if short else 1.25
    tail = 1.2 if short else 3.0

    parts: list[np.ndarray] = [np.zeros(int(lead_in * SR), np.float32)]
    t = lead_in
    words: list[Word] = []
    sentences: list[Sentence] = []
    chapter_starts: list[float] = []
    sent_idx = 0
    prev_ch = -1
    for gi, ((ci, beat), clip) in enumerate(zip(beats, clips)):
        if ci != prev_ch:
            gap = chapter_gap + (title_gap if prev_ch == 0 else 0.0)
            if prev_ch >= 0 and gap:
                parts.append(np.zeros(int(gap * SR), np.float32))
                t += gap
            chapter_starts.append(t)
            prev_ch = ci
        elif gi:
            parts.append(np.zeros(int(beat_gap * SR), np.float32))
            t += beat_gap
        pcm, shift = _trim(decode_pcm(clip.path), clip.words)
        bwords = [(s - shift + t, e - shift + t, w) for s, e, w in clip.words]
        sw = _assign_sentences(beat.text, bwords)
        for k, (sent_text, ws) in enumerate(sw):
            if not ws:
                continue
            for s, e, w in ws:
                words.append(Word(text=w, start=round(s, 3), end=round(e, 3), chapter=ci, beat=gi, sentence=sent_idx))
            sentences.append(Sentence(text=sent_text, start=round(ws[0][0], 3), end=round(ws[-1][1], 3), chapter=ci, beat=gi))
            sent_idx += 1
        parts.append(pcm)
        t += len(pcm) / SR
    parts.append(np.zeros(int(tail * SR), np.float32))
    total = np.concatenate(parts)
    voice_path = out_dir / "voice.wav"
    write_wav(voice_path, total)
    L.info("narration %.1fs, %d words, %d sentences", len(total) / SR, len(words), len(sentences))
    return Timing(duration=round(len(total) / SR, 3), words=words, sentences=sentences,
                  chapter_starts=chapter_starts, voice_path=str(voice_path), lead_in=lead_in)


def _assign_sentences(text: str, words: list[tuple[float, float, str]]) -> list[tuple[str, list]]:
    """Map timed words onto the beat's sentences via the normalized character stream."""
    sents = split_sentences(text) or [text]
    bounds, acc = [], 0
    for s in sents:
        acc += len(norm_key(s))
        bounds.append(acc)
    full = "".join(norm_key(s) for s in sents)
    groups: list[list] = [[] for _ in sents]
    cursor = 0
    for w in words:
        k = norm_key(w[2])
        pos = full.find(k, cursor) if k else -1
        if pos < 0 or pos - cursor > 40:
            pos = cursor
        cursor = pos + len(k)
        idx = next((i for i, b in enumerate(bounds) if pos < b), len(sents) - 1)
        groups[idx].append(w)
    return list(zip(sents, groups))
