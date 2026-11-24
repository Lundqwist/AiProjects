"""Spotify Web API client for AI Workbench.

Sign-in is Authorization Code with PKCE: only the app's Client ID is needed, never a client secret.
The user creates the app at developer.spotify.com with the redirect URI below. After the one-time
sign-in in the browser, the refresh token is kept in %APPDATA%\\AI Workbench\\spotify-token.json.

Playback control needs Spotify Premium, and the Spotify app must be open on some device.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

REDIRECT_PORT = 8888
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"  # Spotify no longer accepts "localhost"
SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
TOKEN_FILE = Path(os.environ.get("APPDATA", Path.home())) / "AI Workbench" / "spotify-token.json"


class SpotifyError(RuntimeError):
    pass


def connected() -> bool:
    return TOKEN_FILE.exists()


def disconnect() -> None:
    TOKEN_FILE.unlink(missing_ok=True)


def _save_token(tok: dict) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TOKEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(tok), encoding="utf-8")
    tmp.replace(TOKEN_FILE)


def _post_form(url: str, form: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SpotifyError(f"Spotify sign-in failed ({e.code}): {e.read().decode(errors='replace')[:200]}")


# ---- sign-in -------------------------------------------------------------------------------
def sign_in(client_id: str, timeout: int = 180) -> str:
    """Open the browser for the user to allow access, wait for the redirect, save the token.
    Returns the Spotify account's display name."""
    client_id = client_id.strip()
    if not client_id:
        raise SpotifyError("Enter the Client ID from your app at developer.spotify.com first.")
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if not self.path.startswith("/callback"):
                self.send_response(404)
                self.end_headers()
                return
            if q.get("state", [""])[0] != state:
                result["error"] = "The sign-in answer didn't match (state). Try again."
            elif "error" in q:
                result["error"] = f"Spotify said: {q['error'][0]}"
            else:
                result["code"] = q.get("code", [""])[0]
            ok = "code" in result
            body = ("<h2>AI Workbench is connected to Spotify.</h2><p>You can close this tab.</p>" if ok else
                    f"<h2>Not connected</h2><p>{result.get('error', '')}</p>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *a):
            pass

    try:
        server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), Handler)
    except OSError:
        raise SpotifyError(f"Port {REDIRECT_PORT} is in use, so the sign-in can't get its answer.")
    server.timeout = 1
    params = {"client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT_URI,
              "scope": SCOPES, "state": state, "code_challenge_method": "S256",
              "code_challenge": challenge}
    webbrowser.open(AUTH_URL + "?" + urllib.parse.urlencode(params))
    end = time.time() + timeout
    try:
        while not result and time.time() < end:
            server.handle_request()
    finally:
        server.server_close()
    if "error" in result:
        raise SpotifyError(result["error"])
    if "code" not in result:
        raise SpotifyError("No answer from the browser in time. Try Connect again.")
    tok = _post_form(TOKEN_URL, {"grant_type": "authorization_code", "code": result["code"],
                                 "redirect_uri": REDIRECT_URI, "client_id": client_id,
                                 "code_verifier": verifier})
    tok["client_id"] = client_id
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    _save_token(tok)
    me = Spotify().get("/me")
    return f"{me.get('display_name') or me.get('id')} ({me.get('product', 'unknown')})"


# ---- API -----------------------------------------------------------------------------------
class Spotify:
    _lock = threading.Lock()

    def _token(self) -> str:
        with self._lock:
            if not TOKEN_FILE.exists():
                raise SpotifyError("Spotify isn't connected. Use Connect in Settings → Spotify.")
            tok = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            if time.time() >= tok.get("expires_at", 0):
                new = _post_form(TOKEN_URL, {"grant_type": "refresh_token",
                                             "refresh_token": tok["refresh_token"],
                                             "client_id": tok["client_id"]})
                tok["access_token"] = new["access_token"]
                tok["refresh_token"] = new.get("refresh_token", tok["refresh_token"])
                tok["expires_at"] = time.time() + int(new.get("expires_in", 3600)) - 60
                _save_token(tok)
            return tok["access_token"]

    def call(self, method: str, path: str, body: dict | None = None, query: dict | None = None):
        url = API + path + ("?" + urllib.parse.urlencode(query) if query else "")
        data = json.dumps(body).encode() if body is not None else (b"" if method in ("PUT", "POST") else None)
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
                return json.loads(raw) if raw.strip() else None
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            try:
                msg = json.loads(text).get("error", {}).get("message", text)
            except ValueError:
                msg = text
            if e.code == 403 and "premium" in msg.lower():
                raise SpotifyError("Controlling playback needs Spotify Premium.")
            if e.code == 404 and "device" in msg.lower():
                raise SpotifyError("No Spotify device is active. Open Spotify on this PC or your phone.")
            raise SpotifyError(f"Spotify error {e.code}: {msg[:200]}")
        except (urllib.error.URLError, socket.timeout) as e:
            raise SpotifyError(f"Can't reach Spotify: {e}")

    def get(self, path, **query):
        return self.call("GET", path, query=query or None)

    # devices and playback
    def devices(self) -> list[dict]:
        return (self.get("/me/player/devices") or {}).get("devices", [])

    def player(self) -> dict | None:
        return self.get("/me/player")

    def search(self, q: str, kind: str = "playlist", limit: int = 5) -> list[dict]:
        res = self.get("/search", q=q, type=kind, limit=limit) or {}
        # Spotify sometimes returns null items in search results
        return [i for i in (res.get(kind + "s") or {}).get("items", []) if i]

    def play(self, device_id: str | None, context_uri: str | None = None, uris: list | None = None):
        body = {}
        if context_uri:
            body["context_uri"] = context_uri
        if uris:
            body["uris"] = uris
        self.call("PUT", "/me/player/play", body or None, {"device_id": device_id} if device_id else None)

    def pause(self):
        self.call("PUT", "/me/player/pause")

    def resume(self):
        self.call("PUT", "/me/player/play")

    def next(self):
        self.call("POST", "/me/player/next")

    def previous(self):
        self.call("POST", "/me/player/previous")

    def volume(self, percent: int):
        self.call("PUT", "/me/player/volume", query={"volume_percent": max(0, min(int(percent), 100))})

    def shuffle(self, on: bool):
        self.call("PUT", "/me/player/shuffle", query={"state": "true" if on else "false"})
