"""Procedural, royalty-free underscore.

Each chapter gets a mood (tension, wonder, somber, driving, uplifting,
neutral) that selects key, mode, progression, tempo and instrumentation.
Sections are rendered with wavetable synthesis (pads, plucks, bells, bass,
light percussion), crossfaded at chapter boundaries, put through a synthetic
convolution reverb, and faded out at the end. Deterministic per video seed.
"""

from __future__ import annotations

import functools
import math
import random

import numpy as np

from ytgen.models import MusicSection

SR = 48000

MOODS = {
    #            root  mode       progression (scale degrees)    bpm  bright pulse  arp   drums  bell
    "tension":   (50, "minor",   [[0, 5, 3, 4], [0, 0, 5, 6]],   84, 0.35, True,  False, "boom", False),
    "wonder":    (52, "lydian",  [[0, 4, 5, 3], [0, 3, 5, 4]],   70, 0.65, False, True,  None,   True),
    "somber":    (45, "minor",   [[0, 3, 5, 4], [0, 5, 3, 4]],   60, 0.30, False, True,  None,   False),
    "driving":   (48, "minor",   [[0, 6, 5, 6], [0, 3, 6, 4]],  112, 0.55, True,  False, "beat", False),
    "uplifting": (55, "major",   [[0, 4, 5, 3], [0, 5, 3, 4]],   96, 0.60, False, True,  "light", True),
    "neutral":   (53, "major",   [[0, 5, 3, 4], [0, 3, 4, 4]],   78, 0.45, False, True,  None,   False),
}
MODES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
}


def midi_hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


@functools.lru_cache(maxsize=16)
def wavetable(bright: float, n: int = 4096) -> np.ndarray:
    """Band-limited saw-ish cycle; `bright` 0..1 sets how many partials / their roll-off."""
    k = np.arange(1, 2 + int(4 + bright * 28))
    ph = np.linspace(0, 2 * np.pi, n, endpoint=False)
    amps = (1.0 / k) * np.exp(-k * (1.2 - bright) * 0.18)
    w = (np.sin(np.outer(k, ph)) * amps[:, None]).sum(0)
    return (w / np.abs(w).max()).astype(np.float32)


def osc(freq: float, n: int, table: np.ndarray, phase: float = 0.0) -> np.ndarray:
    idx = (phase + np.arange(n) * (freq * len(table) / SR)) % len(table)
    return table[idx.astype(np.int32)]


def chord_tones(root: int, mode: str, degree: int, octave_spread: bool = True) -> list[int]:
    sc = MODES[mode]
    notes = []
    for step in (0, 2, 4, 6 if octave_spread else 4):
        d = degree + step
        notes.append(root + sc[d % 7] + 12 * (d // 7))
    return notes


def _env(n: int, a: float, r: float) -> np.ndarray:
    e = np.ones(n, np.float32)
    na, nr = min(n, int(a * SR)), min(n, int(r * SR))
    if na:
        e[:na] = np.linspace(0, 1, na) ** 1.5
    if nr:
        e[-nr:] *= np.linspace(1, 0, nr) ** 1.2
    return e


def _decay(n: int, t60: float) -> np.ndarray:
    return np.exp(-np.arange(n) / SR * (6.9 / t60)).astype(np.float32)


def render_section(mood: str, seconds: float, seed: int) -> np.ndarray:
    root, mode, progs, bpm, bright, pulse, arp, drums, bell = MOODS.get(mood, MOODS["neutral"])
    rng = random.Random(seed)
    prog = progs[seed % len(progs)]
    beat = 60.0 / bpm
    bar = beat * 4
    chord_len = bar * 2 if bpm < 90 else bar
    n_total = int(seconds * SR) + SR
    out = np.zeros((2, n_total), np.float32)
    pad_t = wavetable(bright * 0.8)
    pluck_t = wavetable(min(1.0, bright + 0.25))
    sine_t = wavetable(0.0)
    t = 0.0
    ci = 0
    while t < seconds + 0.5:
        deg = prog[ci % len(prog)]
        notes = chord_tones(root, mode, deg)
        n = int(chord_len * SR)
        s0 = int(t * SR)
        seg = np.zeros((2, n + int(1.5 * SR)), np.float32)
        nn = seg.shape[1]
        # pad: two detuned voices per note, slow swell, overlapping into the next chord
        env = _env(nn, min(1.2, chord_len * 0.4), 1.4)
        for k, m in enumerate(notes):
            for det, pan in ((-0.07, 0.3), (0.07, 0.7)):
                v = osc(midi_hz(m + 12 + det), nn, pad_t, rng.random() * 4096) * env * 0.05
                seg[0] += v * (1 - pan)
                seg[1] += v * pan
        # bass: root an octave down, soft
        bass = osc(midi_hz(notes[0] - 12), nn, sine_t) * _env(nn, 0.05, 1.0) * 0.16
        if pulse:
            gate = ((np.arange(nn) / SR) % (beat / 2) < beat * 0.32).astype(np.float32)
            gate = np.convolve(gate, np.ones(240) / 240, mode="same")
            bass = bass * (0.35 + 0.65 * gate)
        seg += bass
        # arpeggio / bell plucks
        if arp:
            step = beat / 2 if bpm >= 80 else beat
            pattern = [notes[i % 4] + 24 for i in (0, 1, 2, 3, 2, 1)]
            tt, j = 0.0, 0
            while tt < chord_len - 0.05:
                if rng.random() < (0.85 if bpm >= 80 else 0.7):
                    m = pattern[j % len(pattern)]
                    ln = int(1.6 * SR)
                    tbl = sine_t if bell else pluck_t
                    v = osc(midi_hz(m), ln, tbl) * _decay(ln, 1.1 if bell else 0.55) * (0.045 if bell else 0.04)
                    if bell:
                        v += osc(midi_hz(m) * 2.76, ln, sine_t) * _decay(ln, 0.4) * 0.012
                    a = int(tt * SR)
                    pan = 0.3 + 0.4 * rng.random()
                    end = min(nn, a + ln)
                    seg[0, a:end] += v[: end - a] * (1 - pan)
                    seg[1, a:end] += v[: end - a] * pan
                tt += step
                j += 1
        # percussion
        if drums:
            nb = int(round(chord_len / beat))
            for b in range(nb):
                a = int(b * beat * SR)
                if drums == "boom" and b % 8 == 0:
                    _add(seg, _boom(), a, 0.55)
                if drums in ("beat", "light") and (drums == "beat" or b % 2 == 0):
                    _add(seg, _kick(), a, 0.42 if drums == "beat" else 0.3)
                if drums == "beat":
                    _add(seg, _hat(rng), a + int(beat / 2 * SR), 0.10)
        end = min(n_total, s0 + nn)
        out[:, s0:end] += seg[:, : end - s0]
        t += chord_len
        ci += 1
    return out[:, : int(seconds * SR)]


def _add(dst: np.ndarray, src: np.ndarray, at: int, gain: float) -> None:
    end = min(dst.shape[1], at + len(src))
    if end > at:
        dst[:, at:end] += src[: end - at] * gain


@functools.lru_cache(maxsize=1)
def _kick() -> np.ndarray:
    n = int(0.45 * SR)
    t = np.arange(n) / SR
    f = 45 + 80 * np.exp(-t * 28)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return (np.sin(ph) * np.exp(-t * 9)).astype(np.float32)


@functools.lru_cache(maxsize=1)
def _boom() -> np.ndarray:
    n = int(2.5 * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(5)
    noise = np.convolve(rng.normal(0, 1, n), np.ones(400) / 400, mode="same")
    return ((np.sin(2 * np.pi * 52 * t) * 0.9 + noise * 2.0) * np.exp(-t * 2.2)).astype(np.float32)


def _hat(rng: random.Random) -> np.ndarray:
    n = int(0.08 * SR)
    g = np.random.default_rng(rng.randint(0, 1 << 30)).normal(0, 1, n)
    g = g - np.convolve(g, np.ones(6) / 6, mode="same")
    return (g * np.exp(-np.arange(n) / SR * 60)).astype(np.float32)


def reverb(x: np.ndarray, seconds: float = 2.4, wet: float = 0.28, seed: int = 1) -> np.ndarray:
    """Stereo convolution reverb with a synthetic exponentially-decaying IR (overlap-add FFT)."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = np.empty_like(x)
    for ch in range(x.shape[0]):
        ir = rng.normal(0, 1, n) * np.exp(-t * 6.9 / seconds)
        ir = np.convolve(ir, np.ones(8) / 8, mode="same")  # darker tail
        ir[: int(0.012 * SR)] = 0
        ir /= np.sqrt((ir ** 2).sum())
        out[ch] = ola_convolve(x[ch], ir)[: x.shape[1]]
    return (x * (1 - wet) + out * wet * 1.4).astype(np.float32)


def ola_convolve(x: np.ndarray, h: np.ndarray, block: int = 1 << 16) -> np.ndarray:
    nfft = 1 << int(math.ceil(math.log2(block + len(h) - 1)))
    H = np.fft.rfft(h, nfft)
    y = np.zeros(len(x) + len(h) - 1, np.float64)
    for s in range(0, len(x), block):
        seg = x[s:s + block]
        yb = np.fft.irfft(np.fft.rfft(seg, nfft) * H, nfft)[: len(seg) + len(h) - 1]
        y[s:s + len(yb)] += yb
    return y.astype(np.float32)


def score(sections: list[MusicSection], duration: float, seed: int = 0) -> np.ndarray:
    total = int(duration * SR)
    out = np.zeros((2, total), np.float32)
    xf = int(1.6 * SR)
    for i, sec in enumerate(sections):
        a = int(sec.start * SR)
        b = min(total, int(sec.end * SR) + xf)
        if b <= a:
            continue
        part = render_section(sec.mood, (b - a) / SR, seed + i * 7)
        n = part.shape[1]
        env = np.ones(n, np.float32)
        if i > 0:
            env[:xf] = np.linspace(0, 1, xf)
        if i < len(sections) - 1:
            env[-xf:] = np.linspace(1, 0, xf)
        out[:, a:a + n] += part[:, : total - a] * env[: total - a]
    out = reverb(out)
    fade = int(min(4.0, duration * 0.1) * SR)
    out[:, -fade:] *= np.linspace(1, 0, fade) ** 1.5
    out[:, : int(0.8 * SR)] *= np.linspace(0, 1, int(0.8 * SR))
    peak = np.abs(out).max() or 1.0
    return out / peak * 0.7
