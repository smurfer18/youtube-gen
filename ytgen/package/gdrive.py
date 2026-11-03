"""Google Drive storage for project deliverables (Drive API v3).

Safety rules enforced in code:
* every write goes into a subfolder created *inside* the folder id you pass;
  nothing outside it is created, modified, moved or deleted;
* files are never deleted or overwritten; an existing file with the same name in
  the target folder is skipped (no duplicates);
* uploads are verified by reading back the file size from Drive.

Credentials: GDRIVE_CLIENT_ID, GDRIVE_CLIENT_SECRET, GDRIVE_REFRESH_TOKEN
(OAuth "Desktop app" client with the drive.file scope; `ytgen youtube-auth`'s
flow works the same way with that scope).
"""

from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
from typing import Optional

from ytgen.util import log, net

L = log.get("drive")
API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER = "application/vnd.google-apps.folder"


def _token() -> str:
    cid, sec, ref = (os.environ.get(k) for k in ("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN"))
    if not (cid and sec and ref):
        raise RuntimeError("Drive upload needs GDRIVE_CLIENT_ID, GDRIVE_CLIENT_SECRET and GDRIVE_REFRESH_TOKEN")
    r = net.session().post("https://oauth2.googleapis.com/token", timeout=30, data={
        "client_id": cid, "client_secret": sec, "refresh_token": ref, "grant_type": "refresh_token"})
    r.raise_for_status()
    return r.json()["access_token"]


class Drive:
    def __init__(self, root_folder: str):
        self.h = {"Authorization": f"Bearer {_token()}"}
        meta = self._get(root_folder)
        if meta.get("mimeType") != FOLDER:
            raise RuntimeError(f"{root_folder} is not a folder")
        self.root = root_folder

    def _get(self, fid: str) -> dict:
        r = net.session().get(f"{API}/{fid}", headers=self.h, timeout=30,
                              params={"fields": "id,name,mimeType,size,parents", "supportsAllDrives": "true"})
        r.raise_for_status()
        return r.json()

    def _find(self, parent: str, name: str) -> Optional[dict]:
        q = f"'{parent}' in parents and name = '{name.replace(chr(39), chr(92) + chr(39))}' and trashed = false"
        r = net.session().get(API, headers=self.h, timeout=30, params={
            "q": q, "fields": "files(id,name,mimeType,size)", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
        r.raise_for_status()
        f = r.json().get("files", [])
        return f[0] if f else None

    def folder(self, parent: str, name: str) -> str:
        found = self._find(parent, name)
        if found:
            return found["id"]
        r = net.session().post(API, headers=self.h, timeout=30, params={"supportsAllDrives": "true"},
                               json={"name": name, "mimeType": FOLDER, "parents": [parent]})
        r.raise_for_status()
        return r.json()["id"]

    def upload(self, parent: str, path: Path) -> str:
        existing = self._find(parent, path.name)
        if existing:
            L.info("exists, skipped: %s", path.name)
            return existing["id"]
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        size = path.stat().st_size
        r = net.session().post(f"{UPLOAD}?uploadType=resumable&supportsAllDrives=true", timeout=60,
                               headers={**self.h, "X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(size)},
                               json={"name": path.name, "parents": [parent]})
        r.raise_for_status()
        url = r.headers["Location"]
        chunk = 32 * 1024 * 1024
        fid = None
        with open(path, "rb") as f:
            pos = 0
            while pos < size or size == 0:
                data = f.read(chunk)
                end = pos + len(data) - 1
                rr = net.session().put(url, data=data, timeout=600, headers={
                    "Content-Range": f"bytes {pos}-{end}/{size}" if size else "bytes */0"})
                if rr.status_code in (200, 201):
                    fid = rr.json()["id"]
                    break
                if rr.status_code != 308:
                    rr.raise_for_status()
                rng = rr.headers.get("Range")
                pos = int(rng.split("-")[1]) + 1 if rng else end + 1
        meta = self._get(fid)
        if int(meta.get("size", -1)) != size:
            raise RuntimeError(f"verification failed for {path.name}: drive size {meta.get('size')} != {size}")
        L.info("uploaded %s (%.1f MB)", path.name, size / 1e6)
        return fid


def upload_project(project_dir: Path, folder_id: str, include_video: bool = True) -> dict:
    d = Drive(folder_id)
    proj = d.folder(d.root, f"ytgen-{project_dir.name}")
    sub = {k: d.folder(proj, k) for k in ("exports", "metadata", "thumbnails", "sources")}
    out = project_dir / "output"
    ids: dict = {"project_folder": proj}
    for p in sorted(out.glob("*")):
        if p.is_dir():
            continue
        if p.suffix == ".mp4":
            if include_video:
                ids[p.name] = d.upload(sub["exports"], p)
        elif p.suffix == ".jpg":
            ids[p.name] = d.upload(sub["thumbnails"], p)
        elif p.name in ("assets_manifest.json", "CREDITS.md"):
            ids[p.name] = d.upload(sub["sources"], p)
        else:
            ids[p.name] = d.upload(sub["metadata"], p)
    for extra in ("script.json", "plan.json", "qc_report.json", "project.json"):
        if (project_dir / extra).exists():
            ids[extra] = d.upload(sub["metadata"], project_dir / extra)
    (project_dir / "drive.json").write_text(json.dumps(ids, indent=2))
    return ids
