"""TV tools for the voice assistant. The model reads each function's docstring to decide when to call it."""
from __future__ import annotations

import json

import wb_tv

_settings = None


def configure(settings) -> None:
    global _settings
    _settings = settings


def _tv() -> wb_tv.SamsungTV:
    if not (_settings and _settings.tv_ip):
        raise RuntimeError("No TV is set up yet. Use Find TV on the TV tab.")
    return wb_tv.SamsungTV(_settings.tv_ip, _settings.tv_mac)


def _favorites() -> list[dict]:
    return list(_settings.tv_favorites) if _settings else []


def tv_power(action: str) -> str:
    """Turn the TV on or off.

    Args:
        action: "on" or "off"
    """
    tv = _tv()
    if action.lower() in ("on", "start", "på"):
        return "The TV is on." if tv.wake() else "The TV did not wake up."
    tv.power_off()
    return "The TV is turned off."


def tv_watch(what: str) -> str:
    """Show something on the TV: a saved favourite (for example a channel name like CNN), an app
    (Samsung TV Plus, YouTube, TV4 Play, Rakuten TV, Prime Video, Apple TV), or a channel number.
    Turns the TV on first if needed.

    Args:
        what: favourite name, app name or channel number
    """
    tv = _tv()
    w = what.strip().lower()
    for fav in _favorites():
        if fav.get("name", "").lower() == w or fav.get("name", "").lower() in w:
            return "Now showing " + tv.watch(fav.get("app", ""), fav.get("channel", ""))
    for app in wb_tv.APPS:
        if app.lower() == w or app.lower() in w:
            return "Opened " + tv.watch(app=app)
    digits = "".join(c for c in what if c.isdigit())
    if digits:
        return "Now on " + tv.watch(channel=digits)
    names = ", ".join(f["name"] for f in _favorites()) or "none"
    return f"I don't know '{what}'. Favourites: {names}. Apps: {', '.join(wb_tv.APPS)}."


def tv_volume(change: str, steps: int = 3) -> str:
    """Change the TV volume.

    Args:
        change: "up", "down" or "mute"
        steps: how many steps up or down (1-10), ignored for mute
    """
    key = wb_tv.VOLUME_KEYS.get(change.lower())
    if not key:
        return "Say up, down or mute."
    n = 1 if key == "KEY_MUTE" else max(1, min(int(steps), 10))
    _tv().keys([key] * n, delay=0.2)
    return f"Volume {change}" + (f" {n} steps" if n > 1 else "") + "."


def tv_status() -> str:
    """Tell whether the TV is on, and list the saved favourites."""
    if not (_settings and _settings.tv_ip):
        return "No TV is set up yet."
    # Samsung's API can't tell what is showing, only whether the TV is on
    return json.dumps({"tv_device_name": _settings.tv_name or "TV", "power": _tv().state(),
                       "saved_favourites": [f["name"] for f in _favorites()],
                       "note": "What is on screen right now is not known."})


TOOLS = {f.__name__: f for f in (tv_power, tv_watch, tv_volume, tv_status)}
