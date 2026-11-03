"""Shot selection: search every enabled source for each scene, score candidates,
avoid repeats, download, validate, and frame each image (focus point for the
Ken Burns move; full-bleed vs. photo-card framing).

Scenes whose search fails fall back through broader queries (beat ->
chapter -> topic) and finally become kinetic-typography scenes, so a video
never stalls on a missing shot. Low-confidence picks are flagged for review in
the storyboard.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import time
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from ytgen.config import Settings
from ytgen.media import generate as gen
from ytgen.media import library, semantic, sources
from ytgen.media import license as lic
from ytgen.models import Asset, GraphicItem, GraphicSpec, Plan, Script
from ytgen.util import log, net
from ytgen.util.text import STOPWORDS, keywords, kinetic_items, norm_key

L = log.get("media")

Image.MAX_IMAGE_PIXELS = 200_000_000
SEM_FLOOR = 0.205  # CLIP similarity below this: the image does not show the query

_JUNK = re.compile(r"\b(logo|icon|diagram|chart|graph|svg|seal|coat of arms|signature|document|page \d+|scan|"
                   r"stamp|map of|locator|plan of|blueprint|schematic|table|infographic|text|letter|screenshot|"
                   r"cropped|insignia|flag of|banner|poster|cover|title page|manuscript|newspaper)\b", re.I)


def _tokens(s: str) -> set[str]:
    return {norm_key(w) for w in re.findall(r"[A-Za-z0-9]+", s)
            if (len(w) > 2 or w.isdigit()) and w.lower() not in STOPWORDS}


def score(a: Asset, query: str, rank: int, W: int, H: int, vertical: bool) -> float:
    q = _tokens(query)
    t = _tokens(a.title + " " + a.query)
    rel = len(q & t) / max(1, len(q))
    if rel == 0:
        return -9.0  # unrelated: never pick
    sc = 1.6 * rel + 0.35 / (1 + rank)
    qn = {x for x in q if x.isdigit()}
    tn = {x for x in _tokens(a.title) if x.isdigit() and len(x) <= 3}
    if qn and tn and not (qn & tn):
        sc -= 1.0  # "Apollo 13" vs "Apollo 11": a different numbered thing
    if a.width and a.height:
        ar = a.width / a.height
        target = H / W if vertical else W / H
        sc -= 0.35 * abs(math.log(ar / target))
        sc += 0.5 * min(1.0, a.width / (W * 0.9))
        if a.width < W * 0.45:
            sc -= 0.8
    else:
        sc += 0.25
    junk_q = _JUNK.search(query)
    if _JUNK.search(a.title) and not junk_q:
        sc -= 0.9
    if a.kind == "video":
        sc += 0.25  # motion beats stills when equally relevant
    return round(sc, 3)


class Selector:
    def __init__(self, settings: Settings, project_dir: Path, script: Script):
        self.settings = settings
        self.dir = project_dir / "assets"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cache_path = self.dir / "search_cache.json"
        self.cache: dict[str, list[dict]] = json.loads(self.cache_path.read_text()) if self.cache_path.exists() else {}
        self.sources = sources(settings)
        self.script = script
        self.used: set[str] = set()
        self.used_titles: set[str] = set()
        self.used_hashes: set[str] = set()
        self.cooldown: dict[str, float] = {}   # provider/host -> resume time
        self.theme: dict = {}
        self.ledger = gen.Ledger(project_dir, settings.ai_budget_usd)

    # -------------------------------------------------------------- search --
    def search(self, query: str) -> list[Asset]:
        key = f"{'v' if self.settings.vertical else 'h'}|{query.lower().strip()}"
        W, H = self.settings.width, self.settings.height
        if key in self.cache:  # rescore: scoring may have changed since caching
            cached = [Asset.model_validate(a) for a in self.cache[key]]
            ranks: dict[str, int] = {}
            for a in cached:
                ranks[a.provider] = ranks.get(a.provider, -1) + 1
                a.score = score(a, query, ranks[a.provider], W, H, self.settings.vertical)
            return sorted(cached, key=lambda a: -a.score)
        found: list[Asset] = []

        def one(src):
            if self.cooldown.get(src.name, 0) > time.time():
                return []
            try:
                return src.search(query, self.settings.vertical, 12)
            except net.Throttled as e:
                self.cooldown[src.name] = time.time() + min(e.retry_after, 300)
                L.warning("%s is rate-limiting; pausing it for %.0fs", src.name, min(e.retry_after, 300))
                return []
            except Exception as e:
                L.debug("%s failed for %r: %s", src.name, query, e)
                return []

        with ThreadPoolExecutor(len(self.sources) or 1) as ex:
            results = list(ex.map(one, self.sources))
        results.append([a for a in library.search(query, 8) if not a.generated])  # reuse what we own (never AI images)
        for res in results:
            for rank, a in enumerate(res):
                a.score = score(a, query, rank, W, H, self.settings.vertical)
                found.append(a)
        found.sort(key=lambda a: -a.score)
        if found:  # never cache a miss (it may be a transient failure)
            self.cache[key] = [a.model_dump() for a in found[:30]]
        return found[:30]

    def save_cache(self) -> None:
        self.cache_path.write_text(json.dumps(self.cache))

    # ------------------------------------------------------------ download --
    def fetch(self, a: Asset) -> Asset | None:
        if a.path and Path(a.path).exists() and a.provider in ("Local media",) or (a.path and Path(a.path).exists() and a.sha1):
            return self._analyze(a, register=False)
        ext = ".mp4" if a.kind == "video" else ".jpg"
        dest = self.dir / f"{re.sub(r'[^A-Za-z0-9_-]', '_', a.id)[:60]}{ext}"
        try:
            if a.url.startswith("ia:"):
                from ytgen.media.archive import resolve
                a.url = resolve(a)
            host = urlparse(a.url).hostname or ""
            if self.cooldown.get(host, 0) > time.time():
                return None
            if "wikimedia.org" in host and not dest.exists():
                net.pace(host, 0.5)
            net.download(a.url, dest, timeout=180)
        except net.Throttled as e:
            host = e.host
            self.cooldown[host] = time.time() + min(e.retry_after, 300)
            L.warning("%s is rate-limiting downloads; pausing it for %.0fs", host, min(e.retry_after, 300))
            return None
        except Exception as e:
            L.debug("download failed %s: %s", a.url, e)
            return None
        a.path = str(dest)
        if a.kind == "video":
            a = self._trim_video(a)
            if a is None:
                return None
        return self._analyze(a)

    def _trim_video(self, a: Asset, max_len: float = 18.0) -> Asset | None:
        """Long archival films: keep one representative segment (skips leaders/titles)."""
        try:
            dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", a.path],
                                       capture_output=True, text=True, timeout=60).stdout.strip() or 0)
        except Exception:
            return None
        a.duration = dur
        if dur <= max_len * 1.5:
            return a if dur > 2 and _video_ok(a.path, dur) else None
        out = Path(a.path).with_name(Path(a.path).stem + "_clip.mp4")
        base = (hash(a.id) % 100) / 100
        for attempt in range(4):  # try several segments: skip leaders, flares, washed-out film
            start = dur * (0.12 + 0.7 * ((base + 0.27 * attempt) % 1.0)) 
            start = min(start, max(0.0, dur - max_len - 1))
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-i", a.path, "-t", f"{max_len}",
                                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)], capture_output=True)
            if r.returncode == 0 and out.exists() and _video_ok(str(out), max_len):
                Path(a.path).unlink(missing_ok=True)
                a.path, a.duration = str(out), max_len
                return a
        out.unlink(missing_ok=True)
        return None

    def _analyze(self, a: Asset, register: bool = True) -> Asset | None:
        if not a.license_status:
            lic.apply(a)
        if not lic.usable(a, self.settings.allow_uncertain_licenses):
            return None
        if a.kind == "video":
            if not _video_ok(a.path, a.duration or 18.0):
                return None
            if register:
                a.sha1 = library.file_sha1(a.path)
                library.register(a)
            return a
        try:
            with Image.open(a.path) as im:
                im = ImageOps.exif_transpose(im)
                a.width, a.height = im.size
                small = im.convert("L").resize((96, max(1, int(96 * im.height / im.width))))
        except Exception as e:
            L.debug("bad image %s: %s", a.path, e)
            return None
        arr = np.asarray(small, dtype=np.float32)
        if arr.std() < 12 or a.width < 480:     # blank / tiny
            return None
        if (arr > 235).mean() > 0.55:            # mostly white: likely a document or diagram
            return None
        if register or not a.phash:
            a.phash = library.dhash(a.path)
            a.sha1 = a.sha1 or library.file_sha1(a.path)
            library.register(a)
        return a

    # ------------------------------------------------------------ semantic --
    def _semantic(self, query: str, cands: list[Asset]) -> None:
        clip = semantic.get() if self.settings.semantic else None
        if clip is None:
            return
        todo = [c for c in cands if c.semantic is None]
        imgs, keep = [], []
        def load(c):
            if c.path and Path(c.path).exists() and c.kind == "image":
                try:
                    return Image.open(c.path).convert("RGB")
                except Exception:
                    return None
            return semantic.thumb(c.thumb_url) if c.thumb_url else None
        with ThreadPoolExecutor(6) as ex:
            loaded = list(ex.map(load, todo))
        for c, im in zip(todo, loaded):
            if im is not None:
                im.thumbnail((256, 256))
                imgs.append(im)
                keep.append(c)
        for c, sim in zip(keep, clip.similarity(query, imgs)):
            c.semantic = round(sim, 4)

    # --------------------------------------------------------------- choose --
    def choose(self, queries: list[str]) -> tuple[Asset | None, list[Asset], bool]:
        """Return (pick, alternatives, needs_review) trying queries in order."""
        use_sem = self.settings.semantic and semantic.get() is not None
        for qi, q in enumerate(queries):
            if not q:
                continue
            cands = [c for c in self.search(q) if c.score > -5 and c.id not in self.used
                     and _title_key(c.title) not in self.used_titles]
            for c in cands:
                if not c.license_status:
                    lic.apply(c)
            cands = [c for c in cands if c.license_status != "rejected"
                     and (c.license_status != "uncertain" or self.settings.allow_uncertain_licenses)]
            if use_sem and cands:
                top = cands[:10]
                self._semantic(q, top)
                for c in top:
                    if c.semantic is not None:
                        c.score = round(c.score + 7.0 * (c.semantic - 0.24), 3)
                top.sort(key=lambda c: -c.score)
                cands = top + cands[10:]
                # nothing actually shows the subject: try the next (broader) query
                if all((c.semantic or 0) < SEM_FLOOR for c in top if c.semantic is not None) and any(c.semantic is not None for c in top):
                    continue
            pick = None
            for c in cands[:4]:
                if use_sem and c.semantic is not None and c.semantic < SEM_FLOOR:
                    continue
                got = self.fetch(c)
                if got is None:
                    continue
                if got.phash and any(library.hamming(got.phash, h) <= 6 for h in self.used_hashes):
                    continue  # near-duplicate of a shot already in the video
                pick = got
                break
            if pick is not None:
                self.used.add(pick.id)
                self.used_titles.add(_title_key(pick.title))
                if pick.phash:
                    self.used_hashes.add(pick.phash)
                alts = [c for c in cands if c.id != pick.id][:3]
                review = qi > 1 or pick.score < 0.9 or (pick.semantic is not None and pick.semantic < 0.25)
                return pick, alts, review
        return None, [], True

    def generate(self, query: str, seed: int) -> Asset | None:
        if self.settings.ai_images in ("", "off", None):
            return None
        a = gen.generate(query, self.settings.ai_images, self.dir, self.ledger, self.theme, self.settings.width,
                         self.settings.height, seed)
        if a is not None:
            a = self._analyze(a)
        return a


def _video_ok(path: str, dur: float, samples: int = 6) -> bool:
    """Reject clips that are mostly flat (leaders, blown-out or black film): median frame contrast."""
    stds = []
    for k in range(samples):
        t = dur * (k + 0.5) / samples
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1", "-vf", "scale=64:36",
                            "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True)
        if r.returncode == 0 and r.stdout:
            stds.append(float(np.frombuffer(r.stdout, np.uint8).astype(np.float32).std()))
    if len(stds) < samples // 2:
        return False
    flat = sum(1 for x in stds if x < 24)
    return flat <= max(0, int(len(stds) * 0.2))  # at most ~20% of the clip may be flat


def _title_key(t: str) -> str:
    return norm_key(" ".join(t.split()[:4]))


def focus_point(path: str) -> tuple[float, float]:
    """Centre of visual energy (edges), used to aim the Ken Burns move."""
    with Image.open(path) as im:
        g = np.asarray(ImageOps.exif_transpose(im).convert("L").resize((64, 36)), dtype=np.float32)
    gx = np.abs(np.diff(g, axis=1))[:-1, :]
    gy = np.abs(np.diff(g, axis=0))[:, :-1]
    e = gx + gy
    e = e - e.mean()
    e[e < 0] = 0
    if e.sum() < 1e-3:
        return 0.5, 0.5
    ys, xs = np.mgrid[0:e.shape[0], 0:e.shape[1]]
    cx = float((xs * e).sum() / e.sum() / e.shape[1])
    cy = float((ys * e).sum() / e.sum() / e.shape[0])
    return 0.5 + (cx - 0.5) * 0.6, 0.5 + (cy - 0.5) * 0.6


def assign_media(plan: Plan, script: Script, settings: Settings, project_dir: Path, theme: dict | None = None) -> Plan:
    sel = Selector(settings, project_dir, script)
    sel.theme = theme or {}
    if not sel.sources:
        L.warning("no media sources enabled; using typographic scenes")
    topic = script.topic or script.title
    W = plan.width
    KINDS = ("footage", "chapter", "outro", "graphic", "html", "title")
    need = [sc for sc in plan.scenes if sc.kind in KINDS]
    L.info("selecting shots for %d scenes from %s", len(need), ", ".join(s.name for s in sel.sources) or "none")
    prev_asset = None
    for sc in plan.scenes:
        if sc.kind not in KINDS:
            continue
        if sc.kind == "html" and sc.graphic and sc.graphic.type == "map" and sc.graphic.lat is None:
            from ytgen.render.html import geocode
            ll = geocode(sc.graphic.label)
            if ll:
                sc.graphic.lat, sc.graphic.lon = ll
            else:
                sc.kind, sc.graphic = "footage", None
        ch = script.chapters[sc.chapter] if sc.chapter < len(script.chapters) else None
        ch_q = " ".join(keywords(ch.title, 2)) + f" {topic}" if ch else topic
        if sc.kind == "graphic" and prev_asset is not None:
            # graphics sit on a dimmed backdrop: reuse the previous shot, no new search
            sc.asset_id = prev_asset
            continue
        queries = [sc.query, " ".join(sc.query.split()[:3]), ch_q, topic]
        pick, alts, review = sel.choose(list(dict.fromkeys(q.strip() for q in queries if q and q.strip())))
        if pick is None and sc.kind in ("footage", "chapter", "title"):
            pick = sel.generate(sc.query or topic, sc.index)
            if pick is not None:
                alts, review = [], True
                sc.review_reason = "AI-generated image (no licensed footage matched)"
        if pick is None:
            if sc.kind == "footage":
                sc.kind = "kinetic"
                kw = kinetic_items(sc.text) or keywords(sc.text, 3) or sc.text.split()[:3]
                sc.graphic = GraphicSpec(type="kinetic", items=[GraphicItem(label=k) for k in kw])
            sc.needs_review = True
            sc.review_reason = "no licensed footage found"
            continue
        plan.assets[pick.id] = pick
        for a in alts:
            plan.assets.setdefault(a.id, a)
        sc.asset_id = pick.id
        sc.alternatives = [a.id for a in alts]
        sc.needs_review = review
        if not sc.review_reason:
            sc.review_reason = "fallback query or low match score" if review else ""
        prev_asset = pick.id
        if pick.kind == "image" and pick.path:
            fx, fy = focus_point(pick.path)
            m = sc.motion
            dx = m.pan_to[0] - m.pan_from[0]
            dy = m.pan_to[1] - m.pan_from[1]
            m.pan_from = (round(fx - dx / 2, 3), round(fy - dy / 2, 3))
            m.pan_to = (round(fx + dx / 2, 3), round(fy + dy / 2, 3))
            ar = pick.width / max(1, pick.height)
            small = pick.width < W * 0.7
            if (not settings.vertical and (ar < 1.15 or small)) or (settings.vertical and ar > 1.25 and small):
                m.frame = "card"
                m.tilt = round(((sc.index * 7919) % 5 - 2) * 0.9, 2)
            elif settings.vertical and ar > 1.0:
                m.frame = "full"  # landscape in a vertical frame: cover-crop around the focus point
    sel.save_cache()
    found = sum(1 for sc in plan.scenes if sc.asset_id)
    L.info("shots: %d with media, %d typographic, %d flagged for review", found,
           sum(1 for sc in plan.scenes if sc.kind == "kinetic"), sum(1 for sc in plan.scenes if sc.needs_review))
    return plan
