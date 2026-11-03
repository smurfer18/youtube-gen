"""Semantic shot matching with CLIP (ViT-B/32, quantized ONNX; no PyTorch).

Scores how well an image depicts the narration's visual query, so the selector
prefers shots that actually show the subject over ones that merely share
keywords in their titles. Models (~150 MB) download once into the cache.
Optional: if onnxruntime/tokenizers are missing, ranking falls back to keywords.
"""

from __future__ import annotations

import functools
import io
import os
import threading
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from ytgen.util import log, net

L = log.get("semantic")
CACHE = Path(os.environ.get("YTGEN_CACHE", Path.home() / ".cache" / "ytgen")) / "models" / "clip-vit-b32"
BASE = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/"
FILES = {"text.onnx": "onnx/text_model_quantized.onnx", "vision.onnx": "onnx/vision_model_quantized.onnx",
         "tokenizer.json": "tokenizer.json"}
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
_lock = threading.Lock()


class Clip:
    def __init__(self):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        for name, rel in FILES.items():
            net.download(BASE + rel, CACHE / name, timeout=600)
        so = ort.SessionOptions()
        so.intra_op_num_threads = 2
        self.text = ort.InferenceSession(str(CACHE / "text.onnx"), so, providers=["CPUExecutionProvider"])
        self.vision = ort.InferenceSession(str(CACHE / "vision.onnx"), so, providers=["CPUExecutionProvider"])
        self.tok = Tokenizer.from_file(str(CACHE / "tokenizer.json"))
        self.tok.enable_padding(length=77, pad_id=49407)
        self.tok.enable_truncation(77)
        self._tcache: dict[str, np.ndarray] = {}

    def _out(self, sess, feeds) -> np.ndarray:
        names = [o.name for o in sess.get_outputs()]
        want = next((n for n in names if "embeds" in n), names[0])
        v = sess.run([want], feeds)[0]
        v = v.reshape(v.shape[0], -1)
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    def embed_text(self, text: str) -> np.ndarray:
        if text not in self._tcache:
            enc = self.tok.encode(f"a photo of {text}")
            ids = np.array([enc.ids], np.int64)
            feeds = {"input_ids": ids}
            if any(i.name == "attention_mask" for i in self.text.get_inputs()):
                feeds["attention_mask"] = np.array([enc.attention_mask], np.int64)
            self._tcache[text] = self._out(self.text, feeds)[0]
        return self._tcache[text]

    def embed_images(self, imgs: list[Image.Image]) -> np.ndarray:
        batch = []
        for im in imgs:
            im = im.convert("RGB")
            s = 224 / min(im.size)
            im = im.resize((max(224, round(im.width * s)), max(224, round(im.height * s))), Image.BICUBIC)
            l, t = (im.width - 224) // 2, (im.height - 224) // 2
            a = (np.asarray(im.crop((l, t, l + 224, t + 224)), np.float32) / 255 - MEAN) / STD
            batch.append(a.transpose(2, 0, 1))
        return self._out(self.vision, {"pixel_values": np.stack(batch).astype(np.float32)})

    def similarity(self, text: str, imgs: list[Image.Image]) -> list[float]:
        if not imgs:
            return []
        return [float(x) for x in self.embed_images(imgs) @ self.embed_text(text)]


@functools.lru_cache(maxsize=1)
def get() -> Optional[Clip]:
    try:
        with _lock:
            c = Clip()
        L.info("CLIP semantic matching enabled")
        return c
    except Exception as e:
        L.warning("semantic matching unavailable (%s); using keyword ranking", e)
        return None


def thumb(url: str, timeout: float = 15) -> Optional[Image.Image]:
    try:
        r = net.session().get(url, timeout=timeout)
        if r.status_code != 200:
            return None
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:
        return None
