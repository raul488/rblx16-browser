#!/usr/bin/env python3
"""Static server for the browser build.

The engine is compiled with pthreads, so the page needs SharedArrayBuffer,
which browsers only expose to cross-origin-isolated pages.  These two headers
provide that.  Run from anywhere:

    python3 web/serve.py            # http://127.0.0.1:8080

It also lists `web/places/*.rbxl` at `/places` for the menu, and proxies
rbxassetid:// lookups to Roblox assetdelivery and caches the bytes under
web/asset-cache, so places can use remote textures, meshes, sounds and
animations without hitting CORS in the browser.
"""
import gzip
import http.server
import json
import os
import re
import sys
import tempfile
import threading
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
PLACES_DIR = os.path.join(ROOT, "places")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
ASSET_CACHE = os.path.join(ROOT, "asset-cache")
ASSETS_DIR = os.path.join(ROOT, "assets")
ASSET_DELIVERY = os.environ.get(
    "ASSET_DELIVERY_BASE", "https://assetdelivery.roblox.com/v1/asset?id=")
ASSET_COOKIE_FILES = [
    os.path.join(os.path.dirname(ROOT), "asset-cookie.txt"),
    os.path.join(os.path.dirname(ROOT), "asset-cookie"),
]
ASSET_COOKIE_LOCK = threading.Lock()
ASSET_COOKIE_OVERRIDE = {"active": False, "value": ""}
ASSET_PLACE_ID = os.environ.get("ASSET_PLACE_ID", "").strip()
ASSET_ID = re.compile(r"^\d{1,20}$")
MAX_ASSET_BYTES = 64 * 1024 * 1024


def current_cookie():
    """Return (token, source) with the same priority the UI shows."""
    with ASSET_COOKIE_LOCK:
        if ASSET_COOKIE_OVERRIDE["active"]:
            return ASSET_COOKIE_OVERRIDE["value"], "ui"
    token = os.environ.get("ROBLOSECURITY", "").strip()
    if token:
        return token, "environment"
    for path in ASSET_COOKIE_FILES:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                token = f.read().strip()
            if token:
                return token, "file"
    return "", ""


def cookie_status():
    token, source = current_cookie()
    return {"set": bool(token), "source": source}


def list_places():
    """Return the bundled place files for the login list."""
    try:
        names = sorted(os.listdir(PLACES_DIR), key=str.lower)
    except OSError:
        return []
    places = []
    for name in names:
        if not name.lower().endswith((".rbxl", ".rbxlx")):
            continue
        path = os.path.join(PLACES_DIR, name)
        if not os.path.isfile(path):
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        places.append({
            "name": os.path.splitext(name)[0],
            "file": name,
            "bytes": size,
        })
    return places


def normalize_cookie(value):
    value = (value or "").strip()
    if value.startswith(".ROBLOSECURITY="):
        value = value[len(".ROBLOSECURITY="):]
    elif value.startswith("Cookie:"):
        value = value[len("Cookie:"):].strip()
        if value.startswith(".ROBLOSECURITY="):
            value = value[len(".ROBLOSECURITY="):]
    return value.strip().strip('"\'')


def asset_headers():
    """assetdelivery requires a Roblox session for downloads since 2024."""
    token, _ = current_cookie()
    headers = {"User-Agent": "Roblox/WinInet", "Accept": "*/*"}
    if token:
        headers["Cookie"] = ".ROBLOSECURITY=" + token
    return headers


def gunzip_if_needed(data: bytes) -> bytes:
    """assetdelivery sometimes answers gzip without urllib decoding it."""
    for _ in range(3):
        if data[:2] != b"\x1f\x8b":
            break
        try:
            unzipped = gzip.decompress(data)
        except OSError:
            break
        if len(unzipped) < 16 or unzipped == data:
            break
        data = unzipped
    return data


def fetch_asset(asset_id: str) -> bytes:
    """Return asset bytes from the packed store, the cache, or assetdelivery."""
    packed = os.path.join(ASSETS_DIR, asset_id + ".gz")
    if os.path.isfile(packed):
        with open(packed, "rb") as f:
            return gunzip_if_needed(f.read())
    os.makedirs(ASSET_CACHE, exist_ok=True)
    cached = os.path.join(ASSET_CACHE, asset_id)
    if os.path.isfile(cached):
        with open(cached, "rb") as f:
            raw = f.read()
        data = gunzip_if_needed(raw)
        if raw[:2] == b"\x1f\x8b" and data[:2] != b"\x1f\x8b":
            # Old cache entries stored the compressed bytes; replace them.
            try:
                with open(cached, "wb") as f:
                    f.write(data)
            except OSError:
                pass
        return data

    url = ASSET_DELIVERY + asset_id
    if ASSET_PLACE_ID.isdigit():
        url += "&serverplaceid=" + ASSET_PLACE_ID
    request = urllib.request.Request(url, headers=asset_headers())
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise RuntimeError("assetdelivery status %d" % response.status)
        data = response.read(MAX_ASSET_BYTES + 1)
    if len(data) > MAX_ASSET_BYTES:
        raise RuntimeError("asset too large")
    data = gunzip_if_needed(data)
    if data:
        fd, tmp = tempfile.mkstemp(dir=ASSET_CACHE, prefix=asset_id + ".")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, cached)
        except Exception:
            try: os.unlink(tmp)
            except OSError: pass
            raise
    return data


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path.lower()
        if path == "/cookie":
            return self.send_json(cookie_status())
        if path in ("/places", "/places/"):
            return self.send_json(list_places())
        if path in ("/asset", "/asset/"):
            return self.serve_asset()
        return super().do_GET()

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path.lower()
        if path != "/cookie":
            self.send_error(404)
            return
        if self.headers.get("X-RBX-Cookie") != "1":
            self.send_error(403, "missing cookie header")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 8192:
                raise ValueError("bad body")
            raw = self.rfile.read(length).decode("utf-8", "replace")
            if "application/json" in (self.headers.get("Content-Type") or ""):
                value = json.loads(raw).get("value", "")
            else:
                value = (urllib.parse.parse_qs(raw).get("value") or [""])[0]
        except Exception:
            self.send_error(400, "bad cookie body")
            return
        value = normalize_cookie(value)
        if not value or len(value) > 4096 or "\n" in value or "\r" in value:
            self.send_error(400, "bad cookie value")
            return
        path_on_disk = ASSET_COOKIE_FILES[0]
        try:
            fd = os.open(path_on_disk, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(value)
            with ASSET_COOKIE_LOCK:
                ASSET_COOKIE_OVERRIDE["active"] = True
                ASSET_COOKIE_OVERRIDE["value"] = value
        except Exception as exc:
            sys.stderr.write("cookie write failed: %s\n" % exc)
            self.send_error(500, "could not store cookie")
            return
        self.send_json(cookie_status())

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path.lower()
        if path != "/cookie":
            self.send_error(404)
            return
        if self.headers.get("X-RBX-Cookie") != "1":
            self.send_error(403, "missing cookie header")
            return
        with ASSET_COOKIE_LOCK:
            ASSET_COOKIE_OVERRIDE["active"] = False
            ASSET_COOKIE_OVERRIDE["value"] = ""
        for path_on_disk in ASSET_COOKIE_FILES:
            try:
                if os.path.isfile(path_on_disk):
                    os.unlink(path_on_disk)
            except OSError:
                pass
        self.send_json(cookie_status())

    def send_json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_asset(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        values = query.get("id") or query.get("assetId") or []
        asset_id = values[0] if values else ""
        if not ASSET_ID.match(asset_id):
            self.send_error(400, "invalid asset id")
            return
        try:
            data = fetch_asset(asset_id)
        except Exception as exc:
            sys.stderr.write("asset %s failed: %s\n" % (asset_id, exc))
            if not (os.environ.get("ROBLOSECURITY", "").strip() or any(os.path.isfile(p) for p in ASSET_COOKIE_FILES)):
                sys.stderr.write(
                    "  assetdelivery needs a Roblox session: set ROBLOSECURITY "
                    "or write the cookie value to asset-cookie.txt next to web/\n")
            self.send_error(404, "asset fetch failed")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)

    def end_headers(self):
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        # Revalidate instead of no-store: the browser can keep the 73 MB wasm
        # and 107 MB .data and reuse V8's compiled-wasm cache across reloads.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s\n" % (fmt % args))


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
