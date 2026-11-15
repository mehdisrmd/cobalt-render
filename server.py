#!/usr/bin/env python3
"""yt-dlp download engine for Render (replaces the cobalt engine).

Small authenticated HTTP API around yt-dlp:

  GET  /            -> health JSON (no auth)
  POST /download    -> run yt-dlp, stash the file, return a one-shot URL
  GET  /file/<tok>  -> stream a stashed file exactly once

Auth (all routes except GET /):  Authorization: Api-Key <key>
Valid keys are the keys of the JSON object in $API_KEYS_JSON (a JSON
array of key strings is also accepted). Key values are never logged.

Cookies: the boot script converts $YT_COOKIES_JSON (cobalt format) into
/cookies.txt (Netscape); when that file exists it is passed to yt-dlp.
Error output returned to clients is scrubbed of cookie/key values.

Audio mode returns a TRUE MP3 transcoded to the requested bitrate
(FFmpegExtractAudio, ffmpeg is in the image) — the bot's picker
promises MP3, and YouTube's native containers (WebM/Opus, M4A) are
not MP3. Video mode returns the merged MP4, unchanged.
"""

import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PORT = int(os.environ.get("PORT", "10000"))
BASE = (os.environ.get("RENDER_EXTERNAL_URL") or f"http://localhost:{PORT}").rstrip("/")
COOKIES_PATH = "/cookies.txt"
WORK_ROOT = "/tmp/ytdlp-engine"
FILE_TTL_SECONDS = 3600
DOWNLOAD_TIMEOUT_SECONDS = 1200

os.makedirs(WORK_ROOT, exist_ok=True)


def _load_api_keys():
    raw = os.environ.get("API_KEYS_JSON", "").strip()
    keys = set()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                keys = {str(k) for k in data.keys()}
            elif isinstance(data, list):
                keys = {str(k) for k in data}
        except Exception:
            pass
    return keys


API_KEYS = _load_api_keys()


def _load_secrets_for_scrub():
    """Values that must never leak into a client-visible error string."""
    vals = set(API_KEYS)
    raw = os.environ.get("YT_COOKIES_JSON", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                for entries in data.values():
                    items = entries if isinstance(entries, list) else [entries]
                    for entry in items:
                        if isinstance(entry, str):
                            for pair in entry.split(";"):
                                if "=" in pair:
                                    v = pair.split("=", 1)[1].strip()
                                    if len(v) >= 8:
                                        vals.add(v)
        except Exception:
            pass
    return sorted(vals, key=len, reverse=True)


SCRUB_VALUES = _load_secrets_for_scrub()


def scrub(text):
    if not text:
        return text
    for v in SCRUB_VALUES:
        if v:
            text = text.replace(v, "***")
    return text


def _version(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return out.stdout.strip().splitlines()[0].strip()
    except Exception:
        return "unknown"


YTDLP_VERSION = _version(["yt-dlp", "--version"])
NODE_VERSION = _version(["node", "--version"])

download_lock = threading.Lock()
files = {}  # token -> {"path","filename","size","created"}
files_lock = threading.Lock()


def sweep_expired():
    now = time.time()
    with files_lock:
        dead = [t for t, f in files.items() if now - f["created"] > FILE_TTL_SECONDS]
        for t in dead:
            path = files.pop(t)["path"]
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)


def run_download(body):
    url = str(body.get("url") or "").strip()
    if not url or not re.match(r"^https?://", url):
        return 400, {"status": "error", "error": "bad_url"}
    mode = str(body.get("mode") or "audio").strip().lower()
    if mode not in ("audio", "video"):
        return 400, {"status": "error", "error": "bad_mode"}

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "--js-runtimes", "node",
        "--remote-components", "ejs:github",
        "--socket-timeout", "30",
        "--retries", "3",
        "--fragment-retries", "3",
        # Live-replay (was_live) audio/video arrives as HLS. yt-dlp's
        # default hands HLS to ffmpeg's downloader, which crawls from
        # Render's egress and dies on googlevideo 403s after ~10 min
        # (proven on z4TAI3Oy30U, 2026-11-15). The native fragment
        # downloader fetches the same formats at full speed.
        "--hls-prefer-native",
    ]
    if os.path.exists(COOKIES_PATH) and os.path.getsize(COOKIES_PATH) > 0:
        cmd += ["--cookies", COOKIES_PATH]

    if mode == "audio":
        try:
            bitrate = int(str(body.get("bitrate", "128")).strip())
        except ValueError:
            return 400, {"status": "error", "error": "bad_bitrate"}
        bitrate = max(16, min(320, bitrate))
        cmd += ["-f", f"bestaudio[abr<={bitrate}]/bestaudio/best"]
        # Transcode the native container to a true MP3 at the
        # requested bitrate (the picker promises MP3). The
        # postprocessor renames the output to media.mp3 and the
        # native file is dropped, so `produced` below sees the MP3.
        cmd += [
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", f"{bitrate}K",
        ]
    else:
        try:
            quality = int(body.get("quality", 720))
        except (TypeError, ValueError):
            return 400, {"status": "error", "error": "bad_quality"}
        quality = max(144, min(2160, quality))
        cmd += [
            "-f",
            f"bv[height<={quality}]+ba/b[height<={quality}]"
            f"/best[height<={quality}]/best",
            "--merge-output-format", "mp4",
        ]

    job_id = secrets.token_hex(6)
    job_dir = os.path.join(WORK_ROOT, job_id)
    os.makedirs(job_dir, exist_ok=True)
    cmd += ["-o", os.path.join(job_dir, "media.%(ext)s"), url]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=DOWNLOAD_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(job_dir, ignore_errors=True)
        return 504, {"status": "error", "error": "timeout"}

    produced = [
        os.path.join(job_dir, name)
        for name in os.listdir(job_dir)
        if os.path.isfile(os.path.join(job_dir, name))
        and not name.endswith((".part", ".ytdl", ".tmp"))
    ]
    if proc.returncode != 0 or not produced:
        tail = scrub(((proc.stderr or "") + "\n" + (proc.stdout or "")).strip())
        shutil.rmtree(job_dir, ignore_errors=True)
        return 502, {
            "status": "error",
            "error": "download_failed",
            "detail": tail[-1500:],
        }

    path = max(produced, key=os.path.getsize)
    size = os.path.getsize(path)
    filename = os.path.basename(path)
    token = secrets.token_urlsafe(24)
    with files_lock:
        files[token] = {
            "path": path,
            "filename": filename,
            "size": size,
            "created": time.time(),
        }
    return 200, {
        "status": "ok",
        "url": f"{BASE}/file/{token}",
        "filename": filename,
        "size": size,
        "expires_in": FILE_TTL_SECONDS,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep logs, URLs never carry secrets
        print("%s - %s" % (self.address_string(), fmt % args), flush=True)

    def _json(self, code, obj):
        payload = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self):
        header = self.headers.get("Authorization") or ""
        for prefix in ("Api-Key ", "Bearer "):
            if header.startswith(prefix):
                return header[len(prefix):].strip() in API_KEYS
        return False

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._json(200, {
                "status": "ok",
                "engine": "yt-dlp",
                "version": YTDLP_VERSION,
                "node": NODE_VERSION,
                "cookies": os.path.exists(COOKIES_PATH),
                "busy": download_lock.locked(),
            })
        if parsed.path.startswith("/file/"):
            if not self._authorized():
                return self._json(401, {"status": "error", "error": "unauthorized"})
            return self._serve_file(parsed.path[len("/file/"):])
        return self._json(404, {"status": "error", "error": "not_found"})

    def _serve_file(self, token):
        sweep_expired()
        with files_lock:
            entry = files.pop(token, None)
        if not entry:
            return self._json(404, {"status": "error", "error": "not_found"})
        path, filename, size = entry["path"], entry["filename"], entry["size"]
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header(
                "Content-Disposition", f'attachment; filename="{filename}"'
            )
            self.end_headers()
            with open(path, "rb") as fh:
                shutil.copyfileobj(fh, self.wfile, length=256 * 1024)
        finally:
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/download":
            return self._json(404, {"status": "error", "error": "not_found"})
        if not self._authorized():
            return self._json(401, {"status": "error", "error": "unauthorized"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except Exception:
            return self._json(400, {"status": "error", "error": "bad_json"})
        if not download_lock.acquire(blocking=False):
            return self._json(429, {"status": "error", "error": "busy"})
        try:
            sweep_expired()
            code, obj = run_download(body)
        finally:
            download_lock.release()
        return self._json(code, obj)


def main():
    print(
        f"yt-dlp engine {YTDLP_VERSION} (node {NODE_VERSION}) "
        f"listening on :{PORT}, {len(API_KEYS)} api key(s), "
        f"cookies={'yes' if os.path.exists(COOKIES_PATH) else 'no'}",
        flush=True,
    )
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
