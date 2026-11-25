"""The coding team's Runner: runs builds, tests and checks in a job's repository, and nothing else.

The models never write a command. They ask for an action by name ("build", "test", "lint", "typecheck",
"restore"), and the Runner builds the command from the stack pack (packs\\*.toml) for each project it
finds in the repository. So only the commands a pack lists can run, only inside the job's repository,
each with a time limit. Actions marked ask = true are shown on screen first (click only); without an
answer, or with no one to ask, they don't run.

The commands still run the client's own code (build scripts, tests), with your Windows account. To
keep your secrets out of reach, environment variables that look like tokens, keys or passwords are
removed before anything starts.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PACKS_DIR = Path(__file__).resolve().parent / "packs"
SKIP_DIRS = {".git", "node_modules", "bin", "obj", "dist", "build", "vendor", ".venv", "venv", "__pycache__",
             ".vs", ".idea", ".workbench-history", "packages", "out", "coverage", ".next", ".nuxt"}
ACTIONS = ("restore", "build", "test", "lint", "typecheck")
TAIL = 80
SECRET_ENV = re.compile(r"TOKEN|SECRET|PASSW|API_?KEY|_KEY$|CREDENTIAL|PRIVATE|CONNECTIONSTRING|_PAT$|COOKIE",
                        re.I)
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
ERROR_LINE = re.compile(r"\berror\b|\bfail(ed|ing)?\b|✖|not ok|exception|cannot find|is not defined", re.I)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

AskFn = Callable[[str, str, list[str]], str]  # (title, text, buttons) -> the clicked button; last = no


# ---- packs --------------------------------------------------------------------------------------

def load_packs() -> dict[str, dict]:
    packs = {}
    for f in sorted(PACKS_DIR.glob("*.toml")):
        with f.open("rb") as fh:
            p = tomllib.load(fh)
        packs[p["name"]] = p
    return packs


def pack_notes(name: str) -> str:
    return load_packs().get(name, {}).get("notes", {}).get("text", "").strip()


# ---- finding projects ---------------------------------------------------------------------------

@dataclass
class Project:
    pack: str
    kind: str
    file: Path          # the marker file (.sln, .csproj, package.json, composer.json)

    @property
    def dir(self) -> Path:
        return self.file.parent

    def rel(self, root: Path) -> str:
        return self.file.relative_to(root).as_posix()


def _walk(root: Path):
    for here, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        yield Path(here), sorted(files)


def find_projects(repo: Path, packs: dict | None = None) -> list[Project]:
    """Every project in the repository, per pack. A .sln stands for the .csproj files under it."""
    repo = Path(repo).resolve()
    packs = packs or load_packs()
    found: list[Project] = []
    for here, files in _walk(repo):
        for pname, pack in packs.items():
            for kind in pack.get("kind", []):
                for f in files:
                    if any(fnmatch.fnmatch(f, m) for m in kind["markers"]):
                        found.append(Project(pname, kind["name"], here / f))
    slns = [p for p in found if p.file.suffix == ".sln"]
    if slns:  # a .csproj under a .sln's folder is built through the .sln
        found = [p for p in found if not (p.file.suffix == ".csproj"
                                          and any(s.dir in p.file.parents for s in slns))]
    return found


def detect(repo: Path) -> list[str]:
    """The packs that fit the repository, most projects first."""
    counts: dict[str, int] = {}
    for p in find_projects(repo):
        counts[p.pack] = counts.get(p.pack, 0) + 1
    return sorted(counts, key=lambda k: -counts[k])


# ---- conditions ---------------------------------------------------------------------------------

def _package_json(d: Path) -> dict:
    try:
        return json.loads((d / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _composer_json(d: Path) -> dict:
    try:
        return json.loads((d / "composer.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _csprojs(p: Project) -> list[Path]:
    if p.file.suffix == ".csproj":
        return [p.file]
    return [Path(h) / f for h, fs in _walk(p.dir) for f in fs if f.endswith(".csproj")]


def _is_test_project(p: Project) -> bool:
    if p.file.suffix == ".sln":
        return any(_is_test_project(Project(p.pack, p.kind, c)) for c in _csprojs(p))
    try:
        text = p.file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "Microsoft.NET.Test.Sdk" in text or re.search(r"<IsTestProject>\s*true", text, re.I) is not None


def _when(p: Project, cond: str) -> bool:
    what, _, arg = cond.partition(":")
    if what == "script":
        scripts = (_package_json(p.dir) if p.kind == "node" else _composer_json(p.dir)).get("scripts", {})
        s = scripts.get(arg)
        return bool(s) and "no test specified" not in str(s)
    if what == "file":
        return (p.dir / arg).exists()
    if what == "test-project":
        return _is_test_project(p)
    raise ValueError(f"unknown condition {cond!r}")


def _missing(p: Project, need: str) -> str | None:
    """None if the need is met, else what is missing."""
    what, _, arg = need.partition(":")
    if what == "file":
        if p.file.suffix == ".sln" and arg.startswith("obj/"):
            gone = [c for c in _csprojs(p) if not (c.parent / arg).exists()]
            return f"{arg} for {', '.join(c.name for c in gone)}" if gone else None
        return None if (p.dir / arg).exists() else arg
    if what == "deps-installed":
        pj = _package_json(p.dir)
        if not (pj.get("dependencies") or pj.get("devDependencies")):
            return None
        return None if (p.dir / "node_modules").is_dir() else "node_modules"
    raise ValueError(f"unknown need {need!r}")


# ---- planning -----------------------------------------------------------------------------------

@dataclass
class Step:
    action: str
    project: Project
    command: list[str]
    cwd: Path
    timeout: int
    ask: bool = False
    why: str = ""
    missing: str | None = None   # a need that isn't met: the step can't run until a restore
    env: dict[str, str] = field(default_factory=dict)   # extra environment from the pack


def _resolve(exe: str) -> str:
    """npm, npx and composer are .cmd files on Windows: run the real file, not a name the shell guesses."""
    found = shutil.which(exe)
    if not found:
        raise FileNotFoundError(f"'{exe}' is not installed or not on PATH")
    return found


def plan(repo: Path, action: str, only: str | None = None, packs: dict | None = None) -> list[Step]:
    """What `action` would run, project by project. `only` = one project (its relative marker path)."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}: use one of {', '.join(ACTIONS)}")
    repo = Path(repo).resolve()
    packs = packs or load_packs()
    steps = []
    for p in find_projects(repo, packs):
        if only and p.rel(repo) != only.replace("\\", "/"):
            continue
        kind = next(k for k in packs[p.pack]["kind"] if k["name"] == p.kind)
        spec = kind.get("actions", {}).get(action)
        if not spec or not all(_when(p, c) for c in spec.get("when", [])):
            continue
        cmd = [part.replace("{file}", str(p.file)).replace("{dir}", str(p.dir)).replace("{packdir}", str(PACKS_DIR))
               for part in spec["cmd"]]
        missing = next((m for m in (_missing(p, n) for n in spec.get("needs", [])) if m), None)
        steps.append(Step(action, p, cmd, p.dir, int(spec.get("timeout", 300)), bool(spec.get("ask")),
                          spec.get("why", ""), missing, {k: str(v) for k, v in spec.get("env", {}).items()}))
    return steps


# ---- running ------------------------------------------------------------------------------------

@dataclass
class Result:
    action: str
    project: str            # relative marker path
    command: str
    ok: bool
    exit_code: int | None
    seconds: float
    output: str             # the last TAIL lines
    errors: list[str] = field(default_factory=list)  # lines that look like errors (first 20)
    state: str = "ran"      # ran, timed out, needs restore, refused, not installed

    def summary(self) -> str:
        """For a model: what ran, whether it worked, and the lines that matter."""
        head = f"{self.action} · {self.project} · " + (
            "OK" if self.ok else self.state.upper() if self.state != "ran" else f"FAILED (exit {self.exit_code})")
        if self.ok:
            return head
        body = "\n".join(self.errors) if self.errors else self.output
        return f"{head}\n{body}".rstrip()


def clean_env() -> dict[str, str]:
    """Your environment without anything that looks like a secret, and with tools told they run unattended."""
    env = {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}
    env.update(CI="1", NO_COLOR="1", FORCE_COLOR="0", DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1",
               npm_config_audit="false", npm_config_fund="false", npm_config_update_notifier="false")
    return env


STACK = re.compile(r"^\s+at\s|^\s*\S*[\\/]node_modules[\\/]")  # stack frames: about the tool, not the code
NOISE = re.compile(r"^\s*(\d+ (Warning|Error)\(s\)|Time Elapsed|npm notice|Build FAILED\.?$|\}$|errors: \[)", re.I)
CONTEXT = 6  # lines kept after an error line: compilers put the code frame (file:line, the code, a marker) there


def shorten_paths(text: str, repo: Path) -> str:
    """Paths inside the repository as relative ones: shorter for the model, and the same on every PC."""
    repo = Path(repo).resolve()
    for form in (f"file:///{repo.as_posix()}/", f"{repo}\\", f"{repo.as_posix()}/", str(repo)):
        text = re.sub(re.escape(form), "", text, flags=re.I) if form != str(repo) else text.replace(form, ".")
    return text


def error_lines(lines: list[str], limit: int = 30) -> list[str]:
    """Each line that looks like an error, with the lines right after it (the code frame), without repeats."""
    keep: list[int] = []
    for i, ln in enumerate(lines):
        if ERROR_LINE.search(ln):
            keep += range(i, min(i + CONTEXT + 1, len(lines)))
    out = list(dict.fromkeys(lines[i] for i in sorted(set(keep))))
    return out[:limit]


def _kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)


def _inside(path: Path, root: Path) -> bool:
    path, root = Path(path).resolve(), Path(root).resolve()
    return path == root or root in path.parents


def run_step(repo: Path, step: Step, ask_fn: AskFn | None = None) -> Result:
    repo = Path(repo).resolve()
    rel = step.project.rel(repo)
    shown = " ".join(Path(step.command[0]).name if i == 0 else
                     (Path(c).relative_to(repo).as_posix() if os.path.isabs(c) and _inside(c, repo) else c)
                     for i, c in enumerate(step.command))

    def refused(state: str, why: str) -> Result:
        return Result(step.action, rel, shown, False, None, 0.0, why, [why], state)

    if not _inside(step.cwd, repo):
        return refused("refused", f"{step.cwd} is outside the job's repository")
    if step.missing:
        return refused("needs restore", f"{step.missing} is missing: run the restore action first "
                                        "(it asks on screen)")
    if step.ask:
        question = (f"The coding team wants to run <b>{shown}</b> for <b>{rel}</b>.<br><br>"
                    f"It {step.why}.")
        answer = ask_fn("Run a command?", question, ["Allow", "Don't allow"]) if ask_fn else "Don't allow"
        if answer != "Allow":
            return refused("refused", f"not allowed: {shown} needs your OK on screen")
    try:
        cmd = [_resolve(step.command[0])] + step.command[1:]
    except FileNotFoundError as e:
        return refused("not installed", str(e))
    started = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=step.cwd, env={**clean_env(), **step.env}, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=NO_WINDOW | NEW_GROUP)
    try:
        raw, _ = proc.communicate(timeout=step.timeout)
        state, code = "ran", proc.returncode
    except subprocess.TimeoutExpired:
        _kill_tree(proc.pid)
        raw, _ = proc.communicate()
        state, code = "timed out", None
    seconds = time.monotonic() - started
    text = shorten_paths(ANSI.sub("", raw.decode("utf-8", errors="replace")).replace("\r\n", "\n"), repo)
    lines = [ln.rstrip() for ln in text.split("\n") if ln.strip() and not STACK.match(ln) and not NOISE.match(ln)]
    errors = error_lines(lines)
    if state == "timed out":
        errors.insert(0, f"stopped after {step.timeout} s (time limit)")
    return Result(step.action, rel, shown, state == "ran" and code == 0, code, round(seconds, 1),
                  "\n".join(lines[-TAIL:]), errors, state)


def run(repo: Path, action: str, *, only: str | None = None, ask_fn: AskFn | None = None,
        log: Callable[[Result], None] | None = None) -> list[Result]:
    """Run `action` for every project that has it (or the one named by `only`)."""
    results = []
    for step in plan(repo, action, only):
        r = run_step(repo, step, ask_fn)
        results.append(r)
        if log:
            log(r)
    return results


def report(results: list[Result], action: str) -> str:
    """One text for a model or a person: every project's result, failures with their error lines."""
    if not results:
        return f"No project in this repository has a '{action}' action."
    ok = sum(r.ok for r in results)
    return f"{action}: {ok} of {len(results)} OK\n\n" + "\n\n".join(r.summary() for r in results)
