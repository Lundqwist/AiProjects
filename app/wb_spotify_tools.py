"""Spotify tools for the voice assistant. The model reads each function's docstring to decide when
to call it. Offered only when Spotify is connected (Settings → Spotify).

Starting or changing music asks on screen first (like the media keys) unless the user chose
"Always allow"; reading what's playing never asks.
"""
from __future__ import annotations

import json
import socket
import time

import wb_access
import wb_spotify

_settings = None


def configure(settings) -> None:
    global _settings
    _settings = settings


def _allowed(what: str) -> bool:
    if not (_settings and _settings.spotify_ask_first):
        return True
    answer = wb_access.ask("Spotify?", f"Let the assistant <b>{what}</b> on Spotify?",
                           [wb_access.ALLOW_ONCE, wb_access.ALWAYS, wb_access.DENY])
    if answer == wb_access.ALWAYS:
        _settings.spotify_ask_first = False
        _settings.save()
    return answer != wb_access.DENY


def _pick_device(sp: wb_spotify.Spotify) -> dict | None:
    """The active device; else the preferred one (Settings), else this PC, else any."""
    devices = sp.devices()
    if not devices:
        # Nothing is open: start the Spotify app here and wait for it to show up
        try:
            name = wb_access.find_program("Spotify")
            if name:
                wb_access.launch(name)
                for _ in range(20):
                    time.sleep(1)
                    devices = sp.devices()
                    if devices:
                        break
        except Exception:
            pass
    if not devices:
        return None
    active = [d for d in devices if d.get("is_active")]
    if active:
        return active[0]
    wanted = [w.lower() for w in ((_settings.spotify_device if _settings else ""), socket.gethostname()) if w]
    for w in wanted:
        for d in devices:
            if d.get("name", "").lower() == w:
                return d
    return devices[0]


def spotify_play(what: str, kind: str = "playlist") -> str:
    """Play music on Spotify: a genre or mood (country, jazz, chill), an artist, an album, a song
    or a playlist. Starts the Spotify app on this PC if nothing is open.

    Args:
        what: what to play, e.g. "country", "Dolly Parton", "Jolene"
        kind: playlist (best for genres and moods), artist, album or track
    """
    kind = kind.lower().strip()
    if kind not in ("playlist", "artist", "album", "track"):
        kind = "playlist"
    sp = wb_spotify.Spotify()
    found = sp.search(what, kind, limit=5)
    if not found and kind != "playlist":
        kind, found = "playlist", sp.search(what, "playlist", limit=5)
    if not found:
        return f"Spotify found nothing for '{what}'."
    item = found[0]
    label = item.get("name", what)
    if kind == "track":
        label += " by " + ", ".join(a["name"] for a in item.get("artists", []))
    if not _allowed(f"play {label}"):
        return "The user did not allow it."
    device = _pick_device(sp)
    if not device:
        return "No Spotify device is open, and Spotify couldn't be started on this PC."
    if kind == "track":
        sp.play(device["id"], uris=[item["uri"]])
    else:
        sp.play(device["id"], context_uri=item["uri"])
    return f"Playing {kind} {label} on {device.get('name', 'Spotify')}."


def spotify_control(action: str) -> str:
    """Control Spotify playback: pause, resume, next, previous, shuffle on, shuffle off.

    Args:
        action: pause, resume, next, previous, shuffle on or shuffle off
    """
    a = action.lower().strip()
    sp = wb_spotify.Spotify()
    calls = {"pause": sp.pause, "stop": sp.pause, "resume": sp.resume, "play": sp.resume,
             "next": sp.next, "skip": sp.next, "previous": sp.previous, "back": sp.previous,
             "shuffle on": lambda: sp.shuffle(True), "shuffle off": lambda: sp.shuffle(False)}
    fn = calls.get(a)
    if not fn:
        return "Say pause, resume, next, previous, shuffle on or shuffle off."
    if not _allowed(a):
        return "The user did not allow it."
    fn()
    return f"Spotify: {a}."


def spotify_volume(level: str) -> str:
    """Set Spotify's volume (on the device that is playing).

    Args:
        level: a percent 0-100, or "up" / "down" (20 steps)
    """
    sp = wb_spotify.Spotify()
    current = ((sp.player() or {}).get("device") or {}).get("volume_percent")
    l = level.lower().strip().rstrip("%")
    if l in ("up", "louder"):
        target = (current or 50) + 20
    elif l in ("down", "lower", "quieter"):
        target = (current or 50) - 20
    elif l.isdigit():
        target = int(l)
    else:
        return "Say a percent, up or down."
    target = max(0, min(target, 100))
    if not _allowed(f"set the volume to {target}%"):
        return "The user did not allow it."
    sp.volume(target)
    return f"Spotify volume {target}%."


def spotify_now_playing() -> str:
    """Tell what is playing on Spotify right now, and on which device."""
    p = wb_spotify.Spotify().player()
    if not p or not p.get("item"):
        return "Nothing is playing on Spotify."
    item = p["item"]
    artists = ", ".join(a["name"] for a in item.get("artists", []))
    return json.dumps({"track": item.get("name"), "artists": artists,
                       "playing": p.get("is_playing"), "device": (p.get("device") or {}).get("name"),
                       "volume_percent": (p.get("device") or {}).get("volume_percent")})


TOOLS = {f.__name__: f for f in (spotify_play, spotify_control, spotify_volume, spotify_now_playing)}
