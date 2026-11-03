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
import time
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from ytgen.config import Settings
from ytgen.media import sources
from ytgen.models import Asset, GraphicItem, GraphicSpec, Plan, Script
from ytgen.util import log, net
from ytgen.util.text import STOPWORDS, keywords, norm_key

L = log.get("media")

Image.MAX_IMAGE_PIXELS = 200_000_000

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
        self.cooldown: dict[str, float] = {}   # provider/host -> resume time

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
        if a.path and Path(a.path).exists() and a.provider == "Local media":
            return self._analyze(a)
        ext = ".mp4" if a.kind == "video" else ".jpg"
        dest = self.dir / f"{re.sub(r'[^A-Za-z0-9_-]', '_', a.id)[:60]}{ext}"
        host = urlparse(a.url).hostname or ""
        if self.cooldown.get(host, 0) > time.time():
            return None
        try:
            if "wikimedia.org" in host and not dest.exists():
                net.pace(host, 0.5)
            net.download(a.url, dest, timeout=90)
        except net.Throttled as e:
            self.cooldown[host] = time.time() + min(e.retry_after, 300)
            L.warning("%s is rate-limiting downloads; pausing it for %.0fs", host, min(e.retry_after, 300))
            return None
        except Exception as e:
            L.debug("download failed %s: %s", a.url, e)
            return None
        a.path = str(dest)
        return self._analyze(a)

    def _analyze(self, a: Asset) -> Asset | None:
        if a.kind == "video":
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
        return a

    # --------------------------------------------------------------- choose --
    def choose(self, queries: list[str]) -> tuple[Asset | None, list[Asset], bool]:
        """Return (pick, alternatives, needs_review) trying queries in order."""
        for qi, q in enumerate(queries):
            if not q:
                continue
            cands = [c for c in self.search(q) if c.score > -5 and c.id not in self.used and _title_key(c.title) not in self.used_titles]
            pick = None
            for c in cands[:3]:
                got = self.fetch(c)
                if got is not None:
                    pick = got
                    break
            if pick is not None:
                self.used.add(pick.id)
                self.used_titles.add(_title_key(pick.title))
                alts = [c for c in cands if c.id != pick.id][:3]
                review = qi > 0 or pick.score < 0.9
                return pick, alts, review
        return None, [], True


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


def assign_media(plan: Plan, script: Script, settings: Settings, project_dir: Path) -> Plan:
    sel = Selector(settings, project_dir, script)
    if not sel.sources:
        L.warning("no media sources enabled; using typographic scenes")
    topic = script.topic or script.title
    W = plan.width
    need = [sc for sc in plan.scenes if sc.kind in ("footage", "chapter", "outro", "graphic")]
    L.info("selecting shots for %d scenes from %s", len(need), ", ".join(s.name for s in sel.sources) or "none")
    prev_asset = None
    for sc in plan.scenes:
        if sc.kind not in ("footage", "chapter", "outro", "graphic"):
            continue
        ch = script.chapters[sc.chapter] if sc.chapter < len(script.chapters) else None
        ch_q = " ".join(keywords(ch.title, 2)) + f" {topic}" if ch else topic
        if sc.kind == "graphic" and prev_asset is not None:
            # graphics sit on a dimmed backdrop: reuse the previous shot, no new search
            sc.asset_id = prev_asset
            continue
        queries = [sc.query, " ".join(sc.query.split()[:3]), ch_q, topic]
        pick, alts, review = sel.choose(list(dict.fromkeys(q.strip() for q in queries if q and q.strip())))
        if pick is None:
            if sc.kind == "footage":
                sc.kind = "kinetic"
                kw = keywords(sc.text, 4) or [w for w in sc.text.split()[:4]]
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
