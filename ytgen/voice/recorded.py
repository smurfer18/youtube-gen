"""Use your own recorded narration (``--voice file:narration.wav``).

The recording is transcribed with faster-whisper (word timestamps) and the
transcript words are aligned to the script's words with difflib, so each
script beat gets its slice of the recording. Requires ``pip install
faster-whisper``.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from ytgen.config import Settings
from ytgen.util import log
from ytgen.util.text import norm_key
from ytgen.voice import SR, Clip, decode_pcm, register, write_wav

L = log.get("voice")


def transcribe_words(path: Path) -> list[tuple[float, float, str]]:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as e:
        raise RuntimeError("recorded narration needs `pip install faster-whisper`") from e
    model = WhisperModel("small", device="auto", compute_type="int8")
    segs, _ = model.transcribe(str(path), word_timestamps=True, vad_filter=True)
    return [(w.start, w.end, w.word.strip()) for s in segs for w in (s.words or [])]


@register("file")
class RecordedVoice:
    def __init__(self, path: str, settings: Settings):
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(self.path)

    def synth_many(self, texts: list[str], out_dir: Path) -> list[Clip]:
        out_dir.mkdir(parents=True, exist_ok=True)
        pcm = decode_pcm(self.path)
        heard = transcribe_words(self.path)
        L.info("transcribed %d words from %s", len(heard), self.path.name)
        script_words = [(bi, w) for bi, t in enumerate(texts) for w in t.split()]
        a = [norm_key(w) for _, w in script_words]
        b = [norm_key(w[2]) for w in heard]
        # map each script word to a heard word index
        mapping: dict[int, int] = {}
        for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
            for k in range(blk.size):
                mapping[blk.a + k] = blk.b + k
        clips = []
        for bi in range(len(texts)):
            idx = [i for i, (sb, _) in enumerate(script_words) if sb == bi]
            hits = [mapping[i] for i in idx if i in mapping]
            if not hits:
                raise RuntimeError(f"could not find beat {bi} in the recording")
            h0, h1 = min(hits), max(hits)
            start, end = heard[h0][0], heard[h1][1]
            seg = pcm[int(max(0, start - 0.05) * SR):int((end + 0.2) * SR)]
            dest = out_dir / f"{bi:04d}_rec.wav"
            write_wav(dest, seg)
            off = max(0, start - 0.05)
            words = [(heard[j][0] - off, heard[j][1] - off, heard[j][2]) for j in range(h0, h1 + 1)]
            clips.append(Clip(dest, words))
        return clips

