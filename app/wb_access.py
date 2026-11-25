"""What the assistant may do on this PC and network, and how it asks first.

Three levels:
  free     runs at once:  open an allowed program, check status, read tasks, and read-only looks at
           this PC: its name, IP, uptime, CPU/memory/disk use, and the folders and text files in
           the user folder and the code folders (never hidden folders, AppData or key/password files)
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

import wb_lifecycle

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
    # (outside the workbench's job: the program must stay open when the workbench closes)
    wb_lifecycle.start_independent(["explorer.exe", f"shell:AppsFolder\\{app_id}"], creationflags=NO_WINDOW)


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


# ---- this PC: information, folders and files (read-only) ----------------------------------
# Only the user's own folders, and never anything that looks like a key, password or token
SECRET_NAMES = re.compile(r"^\.env|secret|token|password|passwd|credential|\.pem$|\.key$|\.pfx$|\.p12$|"
                          r"^id_(rsa|ed25519|ecdsa|dsa)|\.kdbx$|known_hosts|authorized_keys", re.I)
HIDDEN_PARTS = {"appdata", "ntuser.dat"}
TEXT_EXT = {".txt", ".md", ".log", ".csv", ".json", ".xml", ".yml", ".yaml", ".ini", ".cfg", ".toml", ".html",
            ".htm", ".css", ".js", ".ts", ".jsx", ".tsx", ".py", ".cs", ".csproj", ".sln", ".cpp", ".c", ".h",
            ".hpp", ".java", ".ps1", ".bat", ".cmd", ".sh", ".sql", ".lua", ".toc", ".srt", ".gitignore"}
KNOWN = {"downloads": "Downloads", "documents": "Documents", "desktop": "Desktop", "pictures": "Pictures",
         "music": "Music", "videos": "Videos", "home": "", "hämtade filer": "Downloads",
         "dokument": "Documents", "skrivbordet": "Desktop", "bilder": "Pictures", "musik": "Music"}


def _roots() -> list:
    from pathlib import Path
    home = Path.home()
    roots = [home]
    for extra in [getattr(_settings, "code_projects_dir", "")] + list(getattr(_settings, "code_roots", []) or []):
        if extra:
            roots.append(Path(extra))
    return [r.resolve() for r in roots if r.exists()]


def _resolve(path: str):
    """A path the assistant may look at, or a reason why not. 'downloads' etc. are the user's folders."""
    from pathlib import Path
    raw = (path or "").strip().strip('"')
    known = KNOWN.get(raw.lower().rstrip("/\\"))
    p = (Path.home() / known) if known is not None else Path(os.path.expandvars(os.path.expanduser(raw)))
    if not p.is_absolute():
        p = Path.home() / p
    try:
        p = p.resolve()
    except OSError:
        return None, f"Can't read '{path}'."
    if not any(p == r or r in p.parents for r in _roots()):
        return None, (f"'{p}' is outside the folders I may look in: your user folder and the code folders "
                      f"({', '.join(str(r) for r in _roots())}).")
    rel = p.relative_to(next(r for r in _roots() if p == r or r in p.parents)).parts
    if any(part.startswith(".") or part.lower() in HIDDEN_PARTS or SECRET_NAMES.search(part) for part in rel):
        return None, "That is a hidden or private place (settings, keys or passwords): I don't look there."
    if not p.exists():
        return None, f"'{p}' doesn't exist."
    return p, ""


def _size(n: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


def pc_info() -> str:
    """Facts about this PC right now: name, IP address on the home network, Windows version, how long
    it has been on, CPU and memory use, and free space on each drive. Read-only. Use it for questions
    such as "what's my IP?", "how much space is left?" or "how busy is my PC?"."""
    import platform
    import psutil
    from datetime import datetime
    lan = ""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("192.168.50.1", 9))  # no packet is sent: this only picks the network card used
            lan = sk.getsockname()[0]
    except OSError:
        pass
    vm = psutil.virtual_memory()
    up = datetime.now() - datetime.fromtimestamp(psutil.boot_time())
    drives = {}
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        drives[part.device.rstrip("\\")] = f"{_size(u.free)} free of {_size(u.total)}"
    return json.dumps({
        "pc_name": socket.gethostname(), "ip_address_on_home_network": lan,
        "windows": f"{platform.system()} {platform.release()} (build {platform.version()})",
        "on_for": f"{up.days} days {up.seconds // 3600} h {(up.seconds // 60) % 60} min",
        "cpu_use": f"{psutil.cpu_percent(interval=0.5):.0f} %",
        "memory": f"{_size(vm.used)} used of {_size(vm.total)} ({vm.percent:.0f} %)",
        "drives": drives,
    }, ensure_ascii=False)


def list_folder(path: str = "downloads", newest_first: bool = True) -> str:
    """List what is in a folder on this PC: the user's own folders (downloads, documents, desktop,
    pictures, music, videos, or a full path under the user folder or the code folders). Read-only.

    Args:
        path: downloads, documents, desktop, pictures, music, videos, or a full folder path
        newest_first: newest first (true) or by name (false)
    """
    from datetime import datetime
    p, why = _resolve(path)
    if not p:
        return why
    if not p.is_dir():
        return f"'{p}' is a file, not a folder: use read_text_file to read it."
    items = []
    try:
        for e in p.iterdir():
            if e.name.startswith(".") or SECRET_NAMES.search(e.name) or e.name.lower() in HIDDEN_PARTS:
                continue
            try:
                st = e.stat()
            except OSError:
                continue
            items.append((e, st))
    except PermissionError:
        return f"Windows doesn't let me read '{p}'."
    items.sort(key=(lambda x: -x[1].st_mtime) if newest_first else (lambda x: x[0].name.lower()))
    shown = [{"name": e.name + ("\\" if e.is_dir() else ""),
              "size": "" if e.is_dir() else _size(st.st_size),
              "changed": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")} for e, st in items[:40]]
    return json.dumps({"folder": str(p), "items": len(items), "shown": shown,
                       "instruction": "Sum up in one or two sentences; name only a few items, the newest or "
                                      "the ones asked about."}, ensure_ascii=False)


def find_files(name: str, folder: str = "home") -> str:
    """Find files or folders by (part of) their name under one of the user's folders. Read-only.

    Args:
        name: part of the name, e.g. "invoice" or "*.pdf"
        folder: where to look: home (all of the user folder), downloads, documents, desktop, … or a path
    """
    import fnmatch
    p, why = _resolve(folder)
    if not p:
        return why
    pattern = name if any(c in name for c in "*?") else f"*{name}*"
    hits, seen, start = [], 0, time.time()
    for dirpath, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if not (d.startswith(".") or d.lower() in HIDDEN_PARTS or
                                           d in ("node_modules", "bin", "obj", "__pycache__", ".venv", "venv")
                                           or SECRET_NAMES.search(d))]
        for f in dirs + files:
            seen += 1
            if fnmatch.fnmatch(f.lower(), pattern.lower()) and not SECRET_NAMES.search(f):
                hits.append(os.path.join(dirpath, f))
        if len(hits) >= 30 or time.time() - start > 8:
            break
    return json.dumps({"looked_in": str(p), "found": hits[:30],
                       "note": "stopped early: there may be more" if len(hits) >= 30 or time.time() - start > 8
                       else ""}, ensure_ascii=False)


def read_text_file(path: str) -> str:
    """Read a text file on this PC (notes, Markdown, code, logs, CSV, …) under the user folder or the
    code folders. Read-only; it can't change or delete anything. Long files are cut short.

    Args:
        path: the file's full path, e.g. from list_folder or find_files
    """
    p, why = _resolve(path)
    if not p:
        return why
    if p.is_dir():
        return f"'{p}' is a folder: use list_folder."
    if p.suffix.lower() not in TEXT_EXT and p.name.lower() not in ("readme", "license", "dockerfile"):
        return f"'{p.name}' isn't a text file I can read (PDFs and documents go in Documents: ask about them, or add them in its panel)."
    text = p.read_text(encoding="utf-8", errors="replace")
    return f"{p} ({_size(p.stat().st_size)}):\n" + (text[:6000] + "\n…(cut short)" if len(text) > 6000 else text)


TOOLS = {f.__name__: f for f in (open_program, check_status, add_task, complete_task,
                                 restart_container, media, pc_info, list_folder, find_files, read_text_file)}
