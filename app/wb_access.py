"""What the assistant may do on this PC and network, and how it asks first.

Three levels:
  free     runs at once:  open an allowed program, check status, read tasks
  confirm  asks on screen first (a click, never by voice, so a TV or a visitor can't say yes):
           add or finish a task, restart a container, play/pause and other media keys,
           and opening a program that isn't allowed yet ("Allow once" / "Always allow")
  never    not offered at all: deleting files, sending messages, payments, system settings.
           Programs that could do those (terminals, Registry Editor, Control Panel, Settings, …)
           can never be opened by the assistant, even with permission.

The on-screen question is asked through `ask`, set by the app: ask(title, text, buttons) -> button.
"""
from __future__ import annotations

import ctypes
import difflib
import json
import os
import re
import socket
import subprocess
import time
import urllib.request

NO_WINDOW = 0x08000000

ALLOW_ONCE, ALWAYS, DENY = "Allow once", "Always allow", "Don't allow"
YES, NO = "Yes, do it", "No"

# Never, whatever the user clicks: these can change system settings, run any command or delete
NEVER_PROGRAMS = re.compile(
    r"powershell|command prompt|^cmd\b|terminal|registry|regedit|control panel|^settings$|wsl|"
    r"task scheduler|services|computer management|disk management|group policy|system configuration|"
    r"uninstall|reset|recovery|bash|git bash|node\.js command|developer command|"
    r"windows tools|administrative|event viewer|security|defender|firewall", re.I)

_settings = None
ask = lambda title, text, buttons: buttons[-1]  # noqa: E731 - the app replaces this; default: refuse


def configure(settings, ask_fn=None) -> None:
    global _settings, ask
    _settings = settings
    if ask_fn:
        ask = ask_fn


# ---- programs -----------------------------------------------------------------------------
_apps_cache: tuple[float, dict] | None = None


def start_apps(refresh: bool = False) -> dict[str, str]:
    """Programs in the Start menu: name -> app id (works for desktop and Store apps)."""
    global _apps_cache
    if _apps_cache and not refresh and time.time() - _apps_cache[0] < 600:
        return _apps_cache[1]
    # Windows PowerShell writes in the console's old code page unless told otherwise; one name with
    # an "ä" then broke the decoding and the whole list came back empty
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                          "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=30, creationflags=NO_WINDOW)
    data = json.loads(out.stdout or "[]")
    if isinstance(data, dict):  # a single app comes back as an object, not a list
        data = [data]
    apps = {a["Name"]: a["AppID"] for a in data
            if a.get("Name") and a.get("AppID") and not a["AppID"].startswith("http")}
    if not apps:
        raise RuntimeError("Couldn't read the Start menu's program list.")
    _apps_cache = (time.time(), apps)
    return apps


def is_forbidden(name: str) -> bool:
    return bool(NEVER_PROGRAMS.search(name))


def find_program(spoken: str) -> str | None:
    """Best Start-menu name for what was said ('chrome' -> 'Google Chrome')."""
    apps = start_apps()
    s = spoken.lower().strip()
    exact = [n for n in apps if n.lower() == s]
    if exact:
        return exact[0]
    contains = sorted((n for n in apps if s in n.lower()), key=len)
    if contains:
        return contains[0]
    close = difflib.get_close_matches(s, [n.lower() for n in apps], n=1, cutoff=0.6)
    return next((n for n in apps if n.lower() == close[0]), None) if close else None


def launch(name: str) -> None:
    app_id = start_apps()[name]
    # explorer opens anything in the Start menu by its app id, the same way a click there does
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"], creationflags=NO_WINDOW)


def open_program(name: str) -> str:
    """Open a program on this PC, for example Chrome, Spotify, Discord or Steam. If it isn't on
    the allowed list yet, the user is asked on screen.

    Args:
        name: the program's name as the user said it
    """
    found = find_program(name)
    if not found:
        return f"There is no program called '{name}' in the Start menu."
    if is_forbidden(found):
        return f"{found} is one of the programs I'm never allowed to open."
    allowed = _settings.allowed_programs
    if found not in allowed:
        answer = ask("Open a new program?",
                     f"The assistant wants to open <b>{found}</b>, which isn't on its allowed list.",
                     [ALLOW_ONCE, ALWAYS, DENY])
        if answer == DENY:
            return f"The user did not allow opening {found}."
        if answer == ALWAYS:
            allowed.append(found)
            _settings.save()
    launch(found)
    return f"Opened {found}."


# ---- status ------------------------------------------------------------------------------
def check_status(name: str = "") -> str:
    """Check whether home-network services are up (NAS, Pi-hole, website, …). Read-only.

    Args:
        name: one service to check, or empty for all of them
    """
    targets = [t for t in _settings.status_targets
               if not name or name.lower() in t["name"].lower()] or _settings.status_targets
    results = {}
    for t in targets:
        start = time.time()
        try:
            with socket.create_connection((t["host"], int(t["port"])), timeout=2):
                results[t["name"]] = f"up ({int((time.time() - start) * 1000)} ms)"
        except OSError:
            results[t["name"]] = "DOWN"
    return json.dumps(results)


# ---- tasks (Vikunja) -----------------------------------------------------------------------
def _vikunja(method: str, path: str, body: dict | None = None):
    url = os.environ.get("VIKUNJA_URL", "").rstrip("/") + "/api/v1" + path
    token = os.environ.get("VIKUNJA_TOKEN", "")
    if not (url.startswith("http") and token):
        raise RuntimeError("Vikunja isn't set up (address and token).")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def add_task(title: str, project: str = "Inbox") -> str:
    """Add a task to the user's to-do list (Vikunja). The user confirms on screen first.

    Args:
        title: what the task says
        project: project name, default Inbox
    """
    projects = {p["title"].lower(): p["id"] for p in _vikunja("GET", "/projects")}
    pid = projects.get(project.lower()) or projects.get("inbox")
    if ask("Add a task?", f"Add <b>{title}</b> to {project if project.lower() in projects else 'Inbox'}?",
           [YES, NO]) != YES:
        return "The user did not allow adding the task."
    t = _vikunja("PUT", f"/projects/{pid}/tasks", {"title": title})
    return f"Added task {t['id']}: {t['title']}."


def complete_task(title: str) -> str:
    """Mark one of the user's open tasks as done. The user confirms on screen first.

    Args:
        title: (part of) the task's title
    """
    tasks = _vikunja("GET", "/tasks?filter=done%3Dfalse&per_page=200") or []
    names = {t["title"]: t for t in tasks}
    match = difflib.get_close_matches(title, list(names), n=1, cutoff=0.4) or \
        [n for n in names if title.lower() in n.lower()][:1]
    if not match:
        return f"No open task matches '{title}'."
    task = names[match[0]]
    if ask("Finish a task?", f"Mark <b>{task['title']}</b> as done?", [YES, NO]) != YES:
        return "The user did not allow it."
    task["done"] = True
    _vikunja("POST", f"/tasks/{task['id']}", task)
    return f"Marked '{task['title']}' as done."


# ---- containers ---------------------------------------------------------------------------
def _ssh(host: str, command: str, timeout: int = 40) -> str:
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, command],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-300:])
    return r.stdout


def restart_container(name: str) -> str:
    """Restart a Docker container on the Pi or the NAS (only ones on the allowed list). The user
    confirms on screen first.

    Args:
        name: the container's name, e.g. pihole or vikunja
    """
    allowed = {c["name"]: c for c in _settings.allowed_containers}
    match = difflib.get_close_matches(name.lower(), list(allowed), n=1, cutoff=0.6)
    if not match:
        return f"'{name}' isn't on the list of containers I may restart: {', '.join(allowed) or 'none'}."
    c = allowed[match[0]]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", c["name"]):
        return "That container name isn't valid."
    docker = "/usr/local/bin/docker" if c["host"] == "nas" else "docker"
    if ask("Restart a container?", f"Restart <b>{c['name']}</b> on {c['host']}?", [YES, NO]) != YES:
        return "The user did not allow the restart."
    _ssh(c["host"], f"{docker} restart {c['name']}")
    return f"Restarted {c['name']} on {c['host']}."


# ---- media keys ---------------------------------------------------------------------------
MEDIA_KEYS = {"play": 0xB3, "pause": 0xB3, "play/pause": 0xB3, "next": 0xB0, "previous": 0xB1,
              "stop": 0xB2, "volume up": 0xAF, "volume down": 0xAE, "mute": 0xAD}


def _press(vk: int) -> None:
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)  # key up


def media(action: str) -> str:
    """Control music or video playing on this PC (Spotify, YouTube in Chrome, VLC, …) with the
    media keys. The user confirms on screen first. When the spotify_* tools are offered, use those
    for Spotify instead (they can also pick what to play).

    Args:
        action: play, pause, next, previous, stop, volume up, volume down or mute
    """
    vk = MEDIA_KEYS.get(action.lower().strip())
    if not vk:
        return f"Unknown media action. Use: {', '.join(MEDIA_KEYS)}."
    if ask("Media control?", f"Press <b>{action}</b> on this PC?", [YES, NO]) != YES:
        return "The user did not allow it."
    for _ in range(5 if "volume" in action else 1):
        _press(vk)
    return f"Pressed {action}."


TOOLS = {f.__name__: f for f in (open_program, check_status, add_task, complete_task,
                                 restart_container, media)}
