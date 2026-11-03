"""Final mix: voice chain (high-pass, compression, presence), music ducked
under the voice (sidechain), SFX bus, then two-pass EBU R128 loudness
normalisation to YouTube's -14 LUFS with a -1.5 dBTP ceiling."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np

from ytgen.audio import music as music_mod
from ytgen.audio import sfx as sfx_mod
from ytgen.config import Settings
from ytgen.models import Plan
from ytgen.util import log
from ytgen.voice import write_wav

L = log.get("audio")


def _graph(settings: Settings, has_music: bool, has_sfx: bool, norm: str, has_amb: bool = False,
           music_db: float = -19.0, sfx_db: float = -14.0, amb_db: float = -31.0) -> str:
    g = ("[0:a]highpass=f=75,acompressor=threshold=-21dB:ratio=3:attack=6:release=140:makeup=3,"
         "equalizer=f=3200:t=q:w=1.1:g=2.5,equalizer=f=180:t=q:w=1.0:g=1.2,"
         "aformat=sample_rates=48000:channel_layouts=stereo")
    inputs = ["[v]"]
    if has_music:
        g += ",asplit=2[v][vsc];"
        g += (f"[1:a]volume={music_db}dB[m];"
              "[m][vsc]sidechaincompress=threshold=0.035:ratio=7:attack=40:release=650:makeup=1[md];")
        inputs.append("[md]")
    else:
        g += "[v];"
    idx = 1 + int(has_music)
    if has_sfx:
        g += f"[{idx}:a]volume={sfx_db}dB[s];"
        inputs.append("[s]")
        idx += 1
    if has_amb:
        g += f"[{idx}:a]volume={amb_db}dB,highpass=f=30[amb];"
        inputs.append("[amb]")
    g += f"{''.join(inputs)}amix=inputs={len(inputs)}:normalize=0:duration=first[mix];[mix]{norm}[out]"
    return g


def mix(plan: Plan, voice_path: Path, settings: Settings, out_dir: Path, seed: int = 0) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    inputs = ["-i", str(voice_path)]
    has_music = settings.music and bool(plan.music)
    has_sfx = settings.sfx and bool(plan.sfx)
    if has_music:
        mp = out_dir / "music.wav"
        L.info("composing score: %s", ", ".join(dict.fromkeys(s.mood for s in plan.music)))
        m = music_mod.score(plan.music, plan.duration, seed)
        write_wav(mp, np.ascontiguousarray(m.T).reshape(-1), channels=2)
        inputs += ["-i", str(mp)]
    if has_sfx:
        sp = out_dir / "sfx.wav"
        s = sfx_mod.track(plan.sfx, plan.duration)
        write_wav(sp, np.ascontiguousarray(s.T).reshape(-1), channels=2)
        inputs += ["-i", str(sp)]
    has_amb = bool(plan.extra.get("ambience")) and bool(plan.music)
    if has_amb:
        ap = out_dir / "ambience.wav"
        amb = music_mod.ambience(plan.music, plan.duration, seed)
        write_wav(ap, np.ascontiguousarray(amb.T).reshape(-1), channels=2)
        inputs += ["-i", str(ap)]
    lv = dict(has_amb=has_amb, music_db=plan.extra.get("music_db") or settings.music_db,
              sfx_db=plan.extra.get("sfx_db") or settings.sfx_db, amb_db=plan.extra.get("ambience_db") or -31.0)
    target = f"I={settings.loudness_lufs}:TP=-1.5:LRA=11"
    # pass 1: measure
    g1 = _graph(settings, has_music, has_sfx, f"loudnorm={target}:print_format=json", **lv)
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *inputs, "-filter_complex", g1, "-map", "[out]", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    norm = f"loudnorm={target}"
    if m:
        d = json.loads(m.group(0))
        norm += (f":measured_I={d['input_i']}:measured_TP={d['input_tp']}:measured_LRA={d['input_lra']}"
                 f":measured_thresh={d['input_thresh']}:offset={d['target_offset']}:linear=true")
    out = out_dir / "mix.wav"
    g2 = _graph(settings, has_music, has_sfx, norm, **lv)
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", g2, "-map", "[out]",
                    "-ar", "48000", "-c:a", "pcm_s16le", str(out)], check=True)
    L.info("mix written (target %s LUFS)", settings.loudness_lufs)
    return out
