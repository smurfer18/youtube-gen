"""Persistent, searchable media library shared by all projects (SQLite + FTS5).

Every downloaded asset is registered with its provenance, license status,
content hash and perceptual hash. New projects search the library first, so
footage is reused instead of re-downloaded and duplicates are recognised.

    ytgen library search "saturn v launch"
    ytgen library stats
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageOps

from ytgen.models import Asset

DB = Path(os.environ.get("YTGEN_LIBRARY", Path.home() / ".cache" / "ytgen" / "library.sqlite"))
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY, kind TEXT, provider TEXT, title TEXT, author TEXT, license TEXT, license_status TEXT,
  license_url TEXT, page_url TEXT, url TEXT, path TEXT, sha1 TEXT, phash TEXT, width INT, height INT,
  duration REAL, query TEXT, added REAL, data TEXT);
CREATE INDEX IF NOT EXISTS assets_sha1 ON assets(sha1);
CREATE VIRTUAL TABLE IF NOT EXISTS assets_fts USING fts5(id UNINDEXED, title, query, provider, tokenize='porter');
"""


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB), timeout=30, check_same_thread=False)
    con.executescript(SCHEMA)
    return con


def file_sha1(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def dhash(path: str) -> str:
    """64-bit difference hash: robust to resizing, recompression and small crops."""
    with Image.open(path) as im:
        g = np.asarray(ImageOps.exif_transpose(im).convert("L").resize((9, 8), Image.LANCZOS), dtype=np.int16)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return "%016x" % int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1") if a and b else 64


def register(a: Asset) -> None:
    if not a.path or not Path(a.path).exists():
        return
    with _lock:
        con = connect()
        con.execute("INSERT OR REPLACE INTO assets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            a.id, a.kind, a.provider, a.title, a.author, a.license, a.license_status, a.license_url, a.page_url,
            a.url, a.path, a.sha1, a.phash, a.width, a.height, a.duration, a.query, time.time(),
            a.model_dump_json()))
        con.execute("DELETE FROM assets_fts WHERE id=?", (a.id,))
        con.execute("INSERT INTO assets_fts VALUES (?,?,?,?)", (a.id, a.title, a.query, a.provider))
        con.commit()
        con.close()


def search(query: str, limit: int = 20) -> list[Asset]:
    terms = [t for t in "".join(c if c.isalnum() else " " for c in query).split() if len(t) > 1]
    if not terms or not DB.exists():
        return []
    q = " OR ".join(f'"{t}"' for t in terms)
    with _lock:
        con = connect()
        rows = con.execute("SELECT a.data FROM assets_fts f JOIN assets a ON a.id=f.id WHERE assets_fts MATCH ? "
                           "ORDER BY bm25(assets_fts) LIMIT ?", (q, limit)).fetchall()
        con.close()
    out = []
    for (data,) in rows:
        a = Asset.model_validate_json(data)
        if a.path and Path(a.path).exists():
            out.append(a)
    return out


def get(asset_id: str) -> Optional[Asset]:
    if not DB.exists():
        return None
    with _lock:
        con = connect()
        row = con.execute("SELECT data FROM assets WHERE id=?", (asset_id,)).fetchone()
        con.close()
    if row:
        a = Asset.model_validate_json(row[0])
        return a if a.path and Path(a.path).exists() else None
    return None


def stats() -> dict:
    if not DB.exists():
        return {"assets": 0}
    con = connect()
    n = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    by = dict(con.execute("SELECT provider, COUNT(*) FROM assets GROUP BY provider").fetchall())
    lic = dict(con.execute("SELECT license_status, COUNT(*) FROM assets GROUP BY license_status").fetchall())
    con.close()
    return {"assets": n, "by_provider": by, "by_license": lic, "db": str(DB)}


__all__ = ["register", "search", "get", "stats", "dhash", "hamming", "file_sha1", "json"]
