"""Programming with the code expert and Visual Studio Code.

The code expert (or an online AI for larger jobs) writes whole files in a fixed format; they are
shown as a list of new and changed files with a diff before anything is saved. Saving never
deletes a file and keeps a copy of every file it overwrites in <project>\\.workbench-history.
"""
from __future__ import annotations

import difflib
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import wb_ai
import wb_lifecycle
from wb_settings import Settings

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "bin", "obj", "dist", "build", "__pycache__", ".hf",
             ".vs", ".idea", ".workbench-history", ".python", ".uv-cache", "packages", ".pytest_cache"}
TEXT_SUFFIXES = {".py", ".cs", ".csproj", ".sln", ".cpp", ".c", ".h", ".hpp", ".js", ".jsx", ".ts", ".tsx", ".html",
                 ".css", ".scss", ".json", ".md", ".txt", ".ps1", ".psm1", ".bat", ".cmd", ".sh", ".yml", ".yaml",
                 ".toml", ".ini", ".cfg", ".xml", ".xaml", ".sql", ".lua", ".java", ".kt", ".go", ".rs", ".php",
                 ".rb", ".razor", ".cshtml", ".gitignore", ".editorconfig", ".env.example", ".vue", ".svelte"}
FILE_CHARS = 20000     # one file is cut after this many characters when sent to the expert
PROJECT_CHARS = 60000  # all files together

SYSTEM = """You are an expert programmer and a patient teacher.
When you create or change files, write each COMPLETE file in exactly this form:

=== FILE: relative/path/name.ext ===
```language
the whole file
```

For example:

=== FILE: hello.py ===
```python
print("Hello")
```

Rules: every file gets its own "=== FILE: ... ===" line right before its code block. Whole files only, never
fragments, "..." or "rest unchanged". Only files that are new or changed. Don't tell the user to clone anything.
Paths are relative to the project folder and use forward slashes. Never delete files.
Before the files, explain in short what you did, why, and how to run or test it."""


def vscode() -> str | None:
    found = shutil.which("code")
    for p in [found, Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd",
              Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft VS Code" / "bin" / "code.cmd"]:
        if p and Path(p).exists():
            return str(p)
    return None


def code_cli() -> tuple[list[str], dict]:
    """(the command that runs VS Code's `code` CLI, its environment), without cmd.exe.

    code.cmd is a batch file, and cmd /c mangles quoted paths: with VS Code in "Microsoft VS Code" and a
    file in "AI Workbench", it ran 'C:\\Users\\...\\Microsoft' and failed. So do what code.cmd does:
    run Code.exe as Node on its cli.js (its folder is named after the VS Code version, so it is read
    from code.cmd each time)."""
    code = vscode()
    if not code:
        raise RuntimeError("Visual Studio Code was not found. Install it from code.visualstudio.com.")
    env = {k: v for k, v in os.environ.items() if k != "VSCODE_DEV"}
    p = Path(code)
    if p.suffix.lower() == ".cmd":
        text = p.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'"%~dp0([^"]*?Code(?: - Insiders)?\.exe)"\s+"%~dp0([^"]*?cli\.js)"', text, re.I)
        if m:
            exe, cli = (p.parent / m.group(1)).resolve(), (p.parent / m.group(2)).resolve()
            if exe.exists() and cli.exists():
                env["ELECTRON_RUN_AS_NODE"] = "1"
                return [str(exe), str(cli)], env
    return [code], env  # an unusual install: run it as it is


def open_in_vscode(path: str | Path, file: str | Path | None = None) -> None:
    cli, env = code_cli()
    args = cli + [str(path)] + (["--goto", str(file)] if file else [])
    wb_lifecycle.start_independent(args, env=env, creationflags=wb_lifecycle.NO_WINDOW)


def project_files(folder: Path) -> list[Path]:
    folder = Path(folder)
    if folder.is_file():
        return [folder]
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for f in sorted(files):
            p = Path(root) / f
            if p.suffix.lower() in TEXT_SUFFIXES or f in TEXT_SUFFIXES or f in ("Dockerfile", "Makefile"):
                out.append(p)
    return out


def read_files(folder: Path, only: list[Path] | None = None) -> tuple[str, list[str]]:
    """The files as text for the expert, and notes about what was left out."""
    folder = Path(folder)
    base = folder if folder.is_dir() else folder.parent
    files = only or project_files(folder)
    parts, total, notes = [], 0, []
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if len(text) > FILE_CHARS:
            text = text[:FILE_CHARS] + "\n…(cut here)"
            notes.append(f"{p.relative_to(base).as_posix()} was cut to {FILE_CHARS} characters")
        if total + len(text) > PROJECT_CHARS:
            notes.append(f"{len(files) - len(parts)} more files were left out (too much text); select files to choose")
            break
        rel = p.relative_to(base).as_posix()
        parts.append(f"=== FILE: {rel} ===\n```\n{text}\n```")
        total += len(text)
    return "\n\n".join(parts), notes


@dataclass
class Change:
    path: str              # relative, forward slashes
    new_text: str
    old_text: str | None   # None = a new file

    @property
    def is_new(self) -> bool:
        return self.old_text is None

    def diff(self) -> str:
        old = (self.old_text or "").splitlines(keepends=True)
        return "".join(difflib.unified_diff(old, self.new_text.splitlines(keepends=True),
                                            "(new file)" if self.is_new else f"a/{self.path}", f"b/{self.path}"))


# A line that names one file: "=== FILE: a/b.py ===", "### FILE: b.py ===", "### README.md", "**b.py**", "`b.py`:"
HEADER = re.compile(r"^\s*(?:#{1,6}\s*|\*\*|=+\s*)?(?:file\s*:\s*)?[`*]*"
                    r"((?:[\w.-]+[/\\])*(?:[\w-][\w.-]*\.\w+|Dockerfile|Makefile|\.gitignore|\.editorconfig))"
                    r"[`*]*\s*(?:\*\*|=+)?\s*:?\s*$", re.I)


def _blocks(reply: str) -> list[tuple[int, int, str, str]]:
    """(first line, end line, path, content) per file. A file runs from its heading to the next file
    heading, so code blocks inside it (a README's examples) stay part of it."""
    lines = reply.split("\n")
    heads = []
    for i, line in enumerate(lines):
        m = HEADER.match(line)
        if not m:
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and lines[j].lstrip().startswith("```"):
            heads.append((i, j, m.group(1)))
    out = []
    for n, (i, fence, path) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        close = max((k for k in range(fence + 1, end) if lines[k].strip() == "```"), default=None)
        if close is None:
            continue  # an unfinished file (the reply was cut off): better none than half of one
        out.append((i, close + 1, path, "\n".join(lines[fence + 1:close])))
    return out


def parse(reply: str, folder: Path) -> tuple[str, list[Change]]:
    """(explanation, changes) from the expert's reply. Paths outside the folder are refused."""
    folder = Path(folder).resolve()
    changes, blocks = [], _blocks(reply)
    for _, _, path, content in blocks:
        rel = path.replace("\\", "/").lstrip("/")
        target = (folder / rel).resolve()
        if folder not in target.parents:
            continue
        old = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
        text = content.rstrip("\n") + "\n"
        if old is not None and old.replace("\r\n", "\n") == text:
            continue  # unchanged
        changes = [c for c in changes if c.path != rel] + [Change(rel, text, old)]
    lines = reply.split("\n")
    for start, end, path, _ in reversed(blocks):
        lines[start:end] = [f"*(file: `{path}`)*"]
    return "\n".join(lines).strip(), changes


def apply(folder: Path, changes: list[Change]) -> list[str]:
    """Write the files. Overwritten files are copied to .workbench-history\\<time> first."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    done = []
    for c in changes:
        target = (folder / c.path).resolve()
        if folder.resolve() not in target.parents:
            continue
        if target.exists():
            backup = folder / ".workbench-history" / stamp / c.path
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(c.new_text, encoding="utf-8")
        done.append(c.path)
    return done


def _ask(s: Settings, user: str, big: bool | None, on_text=None) -> wb_ai.Answer:
    return wb_ai.ask(s, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                     task="code", expert="code", big=big, temperature=0.2, max_tokens=16000, on_text=on_text)


def new_project(s: Settings, folder: Path, description: str, big: bool | None = None, on_text=None):
    """(explanation, changes, answer) for a new project in an empty or missing folder."""
    a = _ask(s, f"Create a new project named '{Path(folder).name}'.\n\nWhat it should do:\n{description}\n\n"
                "Include a README.md that says what it is and exactly how to run it on Windows.", big, on_text)
    return (*parse(a.text, folder), a)


def change(s: Settings, folder: Path, request: str, only: list[Path] | None = None, big: bool | None = None,
           on_text=None):
    files, notes = read_files(folder, only)
    base = folder if Path(folder).is_dir() else Path(folder).parent
    a = _ask(s, f"The project's files:\n\n{files}\n\nWhat to change:\n{request}", big, on_text)
    a.notes = notes + a.notes
    return (*parse(a.text, base), a)


def explain(s: Settings, folder: Path, mode: str = "explain", question: str = "", only: list[Path] | None = None,
            big: bool | None = None, on_text=None) -> wb_ai.Answer:
    files, notes = read_files(folder, only)
    task = {"explain": "Explain this code for a learner: what it does, how the parts fit together, and anything "
                       "unusual. Use headings and short code quotes.",
            "review": "Review this code like a senior developer: bugs first (with file and line), then risks, "
                      "then improvements. Be concrete; don't rewrite whole files."}[mode]
    msgs = [{"role": "system", "content": "You are an expert programmer and a patient teacher. Answer in Markdown."},
            {"role": "user", "content": f"{task}\n{question}\n\n{files}"}]
    a = wb_ai.ask(s, msgs, task="code", expert="code", big=big, temperature=0.2, max_tokens=8000, on_text=on_text)
    a.notes = notes + a.notes
    return a


# ---- voice tools ------------------------------------------------------------------------------

_settings: Settings | None = None


def configure(s: Settings) -> None:
    global _settings
    _settings = s


def _find_project(name: str) -> Path | None:
    s = _settings or Settings.load()
    want = re.sub(r"[^a-z0-9]", "", name.lower())
    best = None
    for root in map(Path, s.code_roots):
        if not root.is_dir():
            continue
        for p in [root, *[d for d in root.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))]]:
            have = re.sub(r"[^a-z0-9]", "", p.name.lower())
            if have == want:
                return p
            if want and want in have and best is None:
                best = p
    return best


def open_in_vs_code(project: str) -> str:
    """Open one of the user's code projects in Visual Studio Code, by its folder name.

    Args:
        project: the project's name, e.g. "AiProjects" or "StoryMaker"
    """
    p = _find_project(project)
    if not p:
        return f"No project called {project} in {', '.join((_settings or Settings.load()).code_roots)}."
    open_in_vscode(p)
    return f"Opened {p} in Visual Studio Code."


def safe_name(name: str) -> str:
    return re.sub(r"[^\w.-]+", "-", name).strip("-.") or "project"


def _open_quietly(folder: Path) -> str:
    """Open the folder in VS Code, or in Explorer when VS Code isn't installed."""
    try:
        open_in_vscode(folder)
        return "opened it in Visual Studio Code"
    except RuntimeError:
        os.startfile(folder)  # noqa: S606 - our own folder
        return "opened the folder (Visual Studio Code was not found)"


def _in_projects(project: str) -> Path | None:
    """A project folder directly inside the work folder: the only place voice may change files."""
    s = _settings or Settings.load()
    root = Path(s.code_projects_dir)
    want = re.sub(r"[^a-z0-9]", "", project.lower())
    if not root.is_dir() or not want:
        return None
    dirs = [d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")]
    exact = [d for d in dirs if re.sub(r"[^a-z0-9]", "", d.name.lower()) == want]
    near = [d for d in dirs if want in re.sub(r"[^a-z0-9]", "", d.name.lower())]
    return (exact or near or [None])[0]


def make_code_project(description: str, name: str) -> str:
    """Create new files: a website, program, script or any other code project. The code expert writes
    every file and they are saved in a new folder in the user's work folder, then opened in Visual
    Studio Code. Afterwards tell the user what was made, where, and who wrote it.

    Args:
        description: what to make (e.g. "a one-page website about my cat with a photo gallery"), with every detail the user gave
        name: a short folder name for the project, e.g. "cat-website"
    """
    s = _settings or Settings.load()
    folder = Path(s.code_projects_dir) / safe_name(name)
    if folder.exists() and any(folder.iterdir()):
        return (f"A project called {folder.name} already exists in {s.code_projects_dir}; use change_code_project "
                "to change it, or pick another name.")
    explanation, changes, a = new_project(s, folder, description)
    if not changes:
        return f"The expert gave no files. Its reply: {a.text[:500]}"
    apply(folder, changes)
    opened = _open_quietly(folder)
    return (f"Saved {len(changes)} files in {folder} and {opened}: "
            f"{', '.join(c.path for c in changes)}.\n{a.credit()}\n\nThe expert's notes: {explanation[:1500]}")


def change_code_project(project: str, request: str) -> str:
    """Change or add files in one of the projects in the user's work folder (made earlier with
    make_code_project or the Code tab): fix a bug, add a page to a website, restyle it, and so on.
    Changed files are backed up first; nothing is deleted.

    Args:
        project: the project's folder name, e.g. "cat-website"
        request: what to change, with every detail the user gave
    """
    s = _settings or Settings.load()
    folder = _in_projects(project)
    if not folder:
        return f"No project called {project} in {s.code_projects_dir}. Voice can only change projects there."
    explanation, changes, a = change(s, folder, request)
    if not changes:
        return f"The expert proposed no file changes. Its reply: {a.text[:800]}"
    apply(folder, changes)
    new = [c.path for c in changes if c.is_new]
    changed = [c.path for c in changes if not c.is_new]
    return (f"In {folder}: " + "; ".join(x for x in (f"new {', '.join(new)}" if new else "",
                                                       f"changed {', '.join(changed)}" if changed else "") if x)
            + (" (old versions copied to .workbench-history)" if changed else "")
            + f".\n{a.credit()}\n\nThe expert's notes: {explanation[:1500]}")


def explain_project(project: str, question: str = "") -> str:
    """Read one of the user's code projects and explain it (or answer a question about it).

    Args:
        project: the project's folder name
        question: optional, e.g. "how does the login work?"
    """
    p = _find_project(project)
    if not p:
        return f"No project called {project}."
    a = explain(_settings or Settings.load(), p, "explain", question)
    return f"{a.credit()}\n\n{a.text[:4000]}"


TOOLS = {f.__name__: f for f in (open_in_vs_code, make_code_project, change_code_project, explain_project)}
