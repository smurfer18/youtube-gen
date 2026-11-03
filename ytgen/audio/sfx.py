"""Synthesized sound effects (whoosh, swish, pop, tick, impact, riser, shutter)
placed on the plan's cue times. Original by construction: no samples."""

from __future__ import annotations

import functools

import numpy as np

from ytgen.models import SfxCue

SR = 48000


def _noise(n: int, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).normal(0, 1, n).astype(np.float32)


def _band_sweep(n: int, f0: float, f1: float, q: float, seed: int) -> np.ndarray:
    """Noise through a band-pass whose centre sweeps f0->f1 (STFT-domain shaping)."""
    x = _noise(n, seed)
    hop, win = 256, 1024
    w = np.hanning(win).astype(np.float32)
    frames = max(1, (n - win) // hop + 1)
    out = np.zeros(n + win, np.float32)
    freqs = np.fft.rfftfreq(win, 1 / SR)
    for k in range(frames):
        s = k * hop
        seg = x[s:s + win]
        if len(seg) < win:
            break
        p = k / max(1, frames - 1)
        fc = f0 * (f1 / f0) ** p
        bw = fc / q
        gain = np.exp(-0.5 * ((freqs - fc) / bw) ** 2)
        y = np.fft.irfft(np.fft.rfft(seg * w) * gain, win).astype(np.float32)
        out[s:s + win] += y * w
    return out[:n]


@functools.lru_cache(maxsize=None)
def sound(kind: str) -> np.ndarray:
    """Return a stereo (2, n) float32 effect."""
    if kind in ("whoosh", "swish"):
        dur = 0.6 if kind == "whoosh" else 0.32
        n = int(dur * SR)
        x = _band_sweep(n, 300 if kind == "whoosh" else 900, 3800 if kind == "whoosh" else 6000, 2.2, 11 if kind == "whoosh" else 12)
        t = np.linspace(0, 1, n)
        env = np.sin(np.pi * t ** 0.7) ** 2
        x = x * env
        pan = np.linspace(0.2, 0.8, n)
        st = np.stack([x * (1 - pan), x * pan])
    elif kind == "pop":
        n = int(0.12 * SR)
        t = np.arange(n) / SR
        f = 260 + 520 * np.exp(-t * 60)
        x = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 38)
        st = np.stack([x, x])
    elif kind == "tick":
        n = int(0.05 * SR)
        t = np.arange(n) / SR
        x = (np.sin(2 * np.pi * 1850 * t) + 0.4 * np.sin(2 * np.pi * 3700 * t)) * np.exp(-t * 120)
        st = np.stack([x, x])
    elif kind == "impact":
        n = int(2.2 * SR)
        t = np.arange(n) / SR
        sub = np.sin(2 * np.pi * (38 + 40 * np.exp(-t * 8)) * t) * np.exp(-t * 2.4)
        body = np.convolve(_noise(n, 21), np.ones(160) / 160, mode="same") * 6 * np.exp(-t * 7)
        x = sub * 0.9 + body
        st = np.stack([x, x * 0.96])
    elif kind == "riser":
        n = int(1.4 * SR)
        t = np.linspace(0, 1, n)
        x = _band_sweep(n, 400, 5000, 3.0, 31) * t ** 2.2
        tone = np.sin(2 * np.pi * np.cumsum(200 + 900 * t ** 2) / SR) * 0.15 * t ** 2
        x = x + tone
        st = np.stack([x, x])
    elif kind == "radio":   # two-tone "quindar"-style chirp with a breath of static
        n = int(0.55 * SR)
        t = np.arange(n) / SR
        tone = np.where(t < 0.25, np.sin(2 * np.pi * 2525 * t), np.sin(2 * np.pi * 2475 * t)) * (t < 0.5)
        stat = _band_sweep(n, 1800, 2400, 1.5, 51) * 0.6
        x = (tone * 0.5 + stat * np.exp(-t * 3)) * np.minimum(1, (0.55 - t) * 30)
        st = np.stack([x, x])
    elif kind == "rumble":  # low launch rumble that swells and fades
        n = int(3.5 * SR)
        t = np.linspace(0, 1, n)
        base = np.convolve(_noise(n, 61), np.ones(900) / 900, mode="same") * 30
        x = (base + 0.4 * np.sin(2 * np.pi * 38 * np.arange(n) / SR)) * np.sin(np.pi * t) ** 0.8
        st = np.stack([x, np.roll(x, 400)])
    elif kind == "alarm":   # master-alarm style beeps
        n = int(1.2 * SR)
        t = np.arange(n) / SR
        gate = ((t % 0.3) < 0.15).astype(np.float32)
        x = (np.sin(2 * np.pi * 1450 * t) + 0.3 * np.sin(2 * np.pi * 2900 * t)) * gate * np.exp(-t * 1.2)
        st = np.stack([x, x])
    elif kind == "shutter":
        n = int(0.09 * SR)
        x = _noise(n, 41) * np.exp(-np.arange(n) / SR * 90)
        st = np.stack([x, x])
    else:
        return np.zeros((2, 1), np.float32)
    st = st.astype(np.float32)
    return st / (np.abs(st).max() or 1) * 0.9


def track(cues: list[SfxCue], duration: float) -> np.ndarray:
    out = np.zeros((2, int(duration * SR) + SR), np.float32)
    for c in cues:
        s = sound(c.kind)
        a = int(max(0.0, c.t) * SR)
        end = min(out.shape[1], a + s.shape[1])
        if end > a:
            out[:, a:end] += s[:, : end - a] * (10 ** (c.gain_db / 20))
    return out[:, : int(duration * SR)]
