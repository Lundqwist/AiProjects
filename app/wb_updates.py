"""Startup check: what AI Workbench needs (missing = required) and what has a newer version (optional).

Each finding is an Update. Some are applied while the program runs (Ollama models, voices,
promptfoo); others replace files the running program uses (Python packages, the program's own
code) and are applied by apply_updates.py after the program closes, which then starts it again.
"""
from __future__ import annotations

import glob
import hashlib
import importlib.metadata as md
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: no console flashes


@dataclass
class Update:
    id: str                 # stable key, e.g. "model:gemma3:12b"; "id@version" is what gets skipped
    title: str
    detail: str
    required: bool
    kind: str               # model, voice, package, app, promptfoo, ollama
    target: str = ""        # what to act on (model name, voice name, package spec, …)
    version: str = ""       # the new version, so a skipped update comes back when a newer one appears
    restart: bool = False   # applied after the program closes
    manual: bool = False    # the user has to do it (e.g. run the Ollama installer)
    url: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def skip_key(self) -> str:
        return f"{self.id}@{self.version}"


def _get_json(url: str, timeout: int = 10, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": "AI-Workbench-updater", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _run(args: list[str], cwd: Path | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=NO_WINDOW)


def find_uv() -> str | None:
    """uv installs packages into the venv (a uv-made venv has no pip)."""
    found = shutil.which("uv")
    if found:
        return found
    appdata = os.environ.get("APPDATA", "")
    for pattern in (rf"{appdata}\Python\Python3*\Scripts\uv.exe",
                    rf"{os.environ.get('USERPROFILE', '')}\.local\bin\uv.exe"):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


# ---- checks ------------------------------------------------------------------------------
def check_ollama(settings) -> list[Update]:
    import ollama
    out: list[Update] = []
    try:
        local = {m.model: m.digest for m in ollama.list().models}
        version = _get_json(settings.ollama_host.rstrip("/") + "/api/version", timeout=5)["version"]
    except Exception:
        return [Update("ollama:running", "Ollama is not running",
                       "Start Ollama from the Start menu. Without it nothing in AI Workbench can answer.",
                       True, "ollama", manual=True)]

    needed = dict.fromkeys([settings.chat_model, settings.vision_model, settings.embed_model,
                            settings.agent_model])
    for name in needed:
        full = name if ":" in name else f"{name}:latest"
        if full not in local:
            out.append(Update(f"model:{full}", f"Download the model {name}",
                              "Needed by your settings but not on this PC.", True, "model", target=full))
            continue
        try:  # compare with the registry: a different manifest digest = a newer build
            model, tag = full.split(":", 1)
            path = model if "/" in model else f"library/{model}"
            req = urllib.request.Request(f"https://registry.ollama.ai/v2/{path}/manifests/{tag}", headers={
                "Accept": "application/vnd.docker.distribution.manifest.v2+json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                remote = hashlib.sha256(r.read()).hexdigest()
            if remote != local[full]:
                out.append(Update(f"model:{full}", f"Update the model {name}",
                                  "A newer build of this model is available from Ollama.", False, "model",
                                  target=full, version=remote[:12]))
        except Exception:
            pass  # offline or a model not from the library: nothing to compare

    try:
        latest = _get_json("https://api.github.com/repos/ollama/ollama/releases/latest")["tag_name"].lstrip("v")
        if Version(latest) > Version(version):
            out.append(Update("ollama:app", f"Ollama {latest} is available (you have {version})",
                              "Ollama updates itself from its tray icon, or download the installer.",
                              False, "ollama", version=latest, manual=True, url="https://ollama.com/download"))
    except Exception:
        pass
    return out


def check_voices(settings) -> list[Update]:
    out = []
    for name in dict.fromkeys([settings.voice_en, settings.voice_sv]):
        d = Path(settings.voices_dir)
        if not ((d / f"{name}.onnx").exists() and (d / f"{name}.onnx.json").exists()):
            out.append(Update(f"voice:{name}", f"Download the voice {name}",
                              "Chosen in Settings but not on this PC (about 60 MB).", True, "voice", target=name))
    return out


def check_packages(online: bool = True) -> list[Update]:
    out = []
    reqs = [Requirement(line.split("#")[0].strip()) for line in REQUIREMENTS.read_text().splitlines()
            if line.split("#")[0].strip()]
    for req in reqs:
        try:
            have = md.version(req.name)
        except md.PackageNotFoundError:
            out.append(Update(f"pkg:{req.name}", f"Install the Python package {req.name}",
                              "Needed by the program but not installed.", True, "package",
                              target=str(req), restart=True))
            continue
        if req.specifier and not req.specifier.contains(have, prereleases=True):
            out.append(Update(f"pkg:{req.name}", f"Upgrade {req.name} {have} → {req.specifier}",
                              "The installed version is older than the program needs.", True, "package",
                              target=str(req), restart=True))
            continue
        if not online:
            continue
        try:
            latest = _get_json(f"https://pypi.org/pypi/{req.name}/json", timeout=8)["info"]["version"]
            if Version(latest) > Version(have):
                out.append(Update(f"pkg:{req.name}", f"{req.name} {latest} (you have {have})",
                                  "Optional. New versions can change behaviour: run the self-test after.",
                                  False, "package", target=f"{req.name}=={latest}", version=latest,
                                  restart=True))
        except (OSError, ValueError, KeyError, InvalidVersion):
            pass
    if out and not find_uv():
        for u in out:
            u.manual, u.detail = True, u.detail + " Install uv first: pip install --user uv"
    return out


def check_app() -> list[Update]:
    git = shutil.which("git")
    if not git or not (ROOT / ".git").exists():
        return []
    try:
        if _run([git, "fetch", "--quiet"], ROOT, 30).returncode != 0:
            return []
        behind = _run([git, "rev-list", "--count", "HEAD..@{u}"], ROOT).stdout.strip()
        if not behind or behind == "0":
            return []
        subjects = _run([git, "log", "--format=%s", "-5", "HEAD..@{u}"], ROOT).stdout.strip().splitlines()
        head = _run([git, "rev-parse", "--short", "@{u}"], ROOT).stdout.strip()
        dirty = _run([git, "status", "--porcelain", "--untracked-files=no"], ROOT).stdout.strip()
        detail = "New: " + "; ".join(subjects)
        if dirty:
            detail += ". You have local changes to tracked files: the update is skipped if they conflict."
        return [Update("app:code", f"Program update ({behind} new change{'s' if behind != '1' else ''})",
                       detail, False, "app", version=head, restart=True)]
    except (OSError, subprocess.SubprocessError):
        return []


def check_promptfoo(settings) -> list[Update]:
    path = Path(settings.promptfoo_path)
    npm = shutil.which("npm")
    if not path.exists():
        return [Update("promptfoo", "Install promptfoo (used by the Evaluation tab)",
                       "Only needed for the Evaluation tab.", False, "promptfoo",
                       manual=not npm, version="install")]
    if not npm:
        return []
    try:
        have = _run(["cmd", "/c", str(path), "--version"], timeout=60).stdout.strip().splitlines()[-1]
        latest = _run(["cmd", "/c", npm, "view", "promptfoo", "version"], timeout=60).stdout.strip()
        if Version(latest) > Version(have):
            return [Update("promptfoo", f"promptfoo {latest} (you have {have})",
                           "Optional, for the Evaluation tab.", False, "promptfoo", version=latest)]
    except (OSError, subprocess.SubprocessError, IndexError, InvalidVersion):
        pass
    return []


def check_all(settings, emit=lambda s: None) -> list[Update]:
    found: list[Update] = []
    for label, fn in (("Ollama and models", lambda: check_ollama(settings)),
                      ("voices", lambda: check_voices(settings)),
                      ("Python packages", check_packages),
                      ("program updates", check_app),
                      ("promptfoo", lambda: check_promptfoo(settings))):
        emit(f"Checking {label}…")
        try:
            found += fn()
        except Exception as e:  # one failing check must not hide the others
            emit(f"Could not check {label}: {e}")
    return sorted(found, key=lambda u: (not u.required, u.kind))


# ---- applying (while running) ------------------------------------------------------------
def apply_now(u: Update, settings, emit) -> None:
    if u.kind == "model":
        import ollama
        last = -1
        for part in ollama.pull(u.target, stream=True):
            if part.total and part.completed:
                pct = int(100 * part.completed / part.total)
                if pct != last and pct % 5 == 0:
                    emit(f"{u.target}: {part.status} {pct} %")
                    last = pct
        emit(f"{u.target}: done")
    elif u.kind == "voice":
        from wb_voices import download
        emit(f"Downloading voice {u.target}…")
        download(u.target, settings.voices_dir)
    elif u.kind == "promptfoo":
        emit("Installing promptfoo with npm…")
        r = _run(["cmd", "/c", shutil.which("npm"), "install", "-g", "promptfoo@latest"], timeout=600)
        if r.returncode != 0:
            raise RuntimeError(r.stdout[-500:] + r.stderr[-500:])
    else:
        raise ValueError(f"{u.kind} updates are applied after a restart")


# ---- applying (after the program closes) -------------------------------------------------
def schedule_restart_updates(updates: list[Update]) -> Path:
    """Write the plan and start apply_updates.py, which waits for this process to exit."""
    from wb_settings import SETTINGS_DIR
    scripts = Path(sys.executable).parent  # the venv's Scripts folder (python.exe and pythonw.exe)
    plan = {"pid": os.getpid(), "root": str(ROOT), "uv": find_uv(), "python": str(scripts / "python.exe"),
            "packages": [u.target for u in updates if u.kind == "package"],
            "git_pull": any(u.kind == "app" for u in updates),
            "relaunch": [str(scripts / "pythonw.exe"), str(ROOT / "app" / "main.py")]}
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    plan_file = SETTINGS_DIR / "pending-updates.json"
    plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    subprocess.Popen([str(scripts / "python.exe"), str(ROOT / "app" / "apply_updates.py"), str(plan_file)],
                     cwd=ROOT, creationflags=0x00000010 if os.name == "nt" else 0)  # new console: shows progress
    return plan_file
