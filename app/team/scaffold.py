"""New projects from a stack pack's templates, before the coding team starts.

    python app\\team\\cli.py scaffold E:\\DropShipV1 --template dotnet-react --name DropShip --allow-restore --commit

The team is good at changing a project that builds and much worse at making one from nothing: seven runs of a new
web shop failed at the first step (project files in the wrong place, a project that referenced itself, references
to paths that didn't exist). A person starts with a template, so the team does too. A template is a folder under
packs\\<pack>\\templates\\<name>\\: its files are copied with {{Name}}, {{name}}, {{ApiPort}} and {{HttpsPort}}
filled in (in paths too). Nothing is overwritten: if any file already exists, nothing is written.

Templates:
  web/dotnet-react   src\\<Name>.Api (ASP.NET Core 8 Web API with controllers, a health endpoint,
                     `public partial class Program`), tests\\<Name>.Tests (xUnit + WebApplicationFactory, one
                     passing test), client (React 18 + Vite + React Router, /api proxied to the API)
"""
from __future__ import annotations

import random
from pathlib import Path

from team import jobs, runner

TEMPLATES = runner.PACKS_DIR


def available() -> dict[str, Path]:
    """'pack/name' -> template folder."""
    out = {}
    for pack in sorted(p for p in TEMPLATES.iterdir() if p.is_dir()):
        for t in sorted((pack / "templates").glob("*")) if (pack / "templates").is_dir() else []:
            if t.is_dir():
                out[f"{pack.name}/{t.name}"] = t
    return out


def _fill(text: str, values: dict[str, str]) -> str:
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def plan(template: str, target: Path, name: str, api_port: int | None = None) -> dict[str, str]:
    """relative path -> file text, for every file the template makes."""
    found = available()
    key = template if "/" in template else next((k for k in found if k.endswith("/" + template)), template)
    if key not in found:
        raise ValueError(f"no template {template!r}; there are: {', '.join(found) or 'none'}")
    if not name.replace(".", "").replace("_", "").isalnum() or not name[0].isalpha():
        raise ValueError(f"{name!r} can't be a project name: letters and digits only, starting with a letter")
    port = api_port or random.Random(name).randint(5100, 5899)   # the same name gives the same port
    values = {"Name": name, "name": name.lower(), "ApiPort": str(port), "HttpsPort": str(port + 1000)}
    files = {}
    root = found[key]
    for f in sorted(root.rglob("*")):
        if f.is_file():
            rel = _fill(f.relative_to(root).as_posix(), values)
            files[rel] = _fill(f.read_text(encoding="utf-8"), values)
    return files


def make(template: str, target: Path, name: str, api_port: int | None = None) -> list[str]:
    """Write the template's files into `target`. Refuses (writes nothing) if any of them exists."""
    target = Path(target)
    files = plan(template, target, name, api_port)
    there = [rel for rel in files if (target / rel).exists()]
    if there:
        raise FileExistsError(f"these files already exist, so nothing was written: {', '.join(there[:8])}")
    for rel, text in files.items():
        p = target / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return sorted(files)


def verify(target: Path, ask_fn: runner.AskFn | None) -> tuple[bool, str]:
    """Restore (asks), build and test the new projects. (ok, report)"""
    reports, ok = [], True
    for action in ("restore", "build", "test"):
        rs = runner.run(target, action, ask_fn=ask_fn)
        reports.append(runner.report(rs, action))
        if rs and not all(r.ok for r in rs):
            ok = False
            break
    return ok, "\n\n".join(reports)


def commit(target: Path, message: str) -> str:
    jobs.git(target, "add", "-A")
    jobs.git(target, "-c", "user.name=AI Workbench", "-c", "user.email=workbench@localhost", "commit", "--quiet",
             "-m", message)
    return jobs.git(target, "rev-parse", "--short", "HEAD")
