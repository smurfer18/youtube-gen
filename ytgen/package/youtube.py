"""YouTube Data API v3 publishing (resumable upload, thumbnail, captions,
scheduling) with an OAuth2 refresh token.

One-time setup: create an OAuth client (Desktop app) in Google Cloud with the
YouTube Data API enabled, download its JSON, then run
``ytgen youtube-auth client_secret.json``. It prints the refresh token to put
in YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN.
"""

from __future__ import annotations

import http.server
import json
import os
import threading
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Optional

from ytgen.util import log, net

L = log.get("youtube")
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.force-ssl"


def access_token() -> str:
    cid, sec, ref = (os.environ.get(k) for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"))
    if not (cid and sec and ref):
        raise RuntimeError("set YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET and YOUTUBE_REFRESH_TOKEN (see `ytgen youtube-auth`)")
    r = net.session().post(TOKEN_URL, data={"client_id": cid, "client_secret": sec, "refresh_token": ref,
                                            "grant_type": "refresh_token"}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def upload(video: Path, metadata: dict, thumbnail: Optional[Path] = None, captions: Optional[Path] = None,
           privacy: str = "private", publish_at: Optional[str] = None) -> str:
    tok = access_token()
    body = {"snippet": metadata["snippet"], "status": dict(metadata.get("status", {}))}
    body["status"]["privacyStatus"] = "private" if publish_at else privacy
    if publish_at:
        body["status"]["publishAt"] = publish_at
    size = video.stat().st_size
    s = net.session()
    r = s.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
               headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8",
                        "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
               data=json.dumps(body), timeout=60)
    r.raise_for_status()
    url = r.headers["Location"]
    chunk = 16 * 1024 * 1024
    with open(video, "rb") as f:
        pos = 0
        while pos < size:
            data = f.read(chunk)
            end = pos + len(data) - 1
            rr = s.put(url, data=data, timeout=600, headers={"Authorization": f"Bearer {tok}",
                                                           "Content-Range": f"bytes {pos}-{end}/{size}"})
            if rr.status_code in (200, 201):
                vid = rr.json()["id"]
                break
            if rr.status_code != 308:
                rr.raise_for_status()
            rng = rr.headers.get("Range")
            pos = int(rng.split("-")[1]) + 1 if rng else end + 1
            L.info("uploaded %.0f%%", 100 * pos / size)
    L.info("video id %s", vid)
    if thumbnail and thumbnail.exists():
        s.post(f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={vid}",
               headers={"Authorization": f"Bearer {tok}", "Content-Type": "image/jpeg"},
               data=thumbnail.read_bytes(), timeout=120).raise_for_status()
    if captions and captions.exists():
        meta = {"snippet": {"videoId": vid, "language": metadata["snippet"].get("defaultLanguage", "en"), "name": "Captions"}}
        boundary = "ytgen-boundary"
        payload = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
                   f"--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n").encode() + captions.read_bytes() + f"\r\n--{boundary}--".encode()
        s.post("https://www.googleapis.com/upload/youtube/v3/captions?uploadType=multipart&part=snippet",
               headers={"Authorization": f"Bearer {tok}", "Content-Type": f"multipart/related; boundary={boundary}"},
               data=payload, timeout=120).raise_for_status()
    return vid


def authorize(client_secrets: Path, port: int = 8765) -> str:
    cfg = json.loads(client_secrets.read_text())
    cfg = cfg.get("installed") or cfg.get("web") or cfg
    redirect = f"http://127.0.0.1:{port}/"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cfg["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent"})
    got: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got["code"] = q.get("code", [""])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ytgen: authorized, you can close this tab.")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    th = threading.Thread(target=srv.handle_request, daemon=True)
    th.start()
    print("Open this URL and approve access:\n\n" + url + "\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    th.join()
    r = net.session().post(TOKEN_URL, data={"code": got["code"], "client_id": cfg["client_id"],
                                            "client_secret": cfg["client_secret"], "redirect_uri": redirect,
                                            "grant_type": "authorization_code"}, timeout=30)
    r.raise_for_status()
    return r.json()["refresh_token"]
