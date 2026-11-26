"""The Scout: reads the repository and tells the Builder what a step needs, before any code is written.

It gets a map of the repository (every file with its classes, functions and endpoints, by line) and returns:
  requirements  the step split into behaviours, one each, so nothing in a long task is forgotten
  places        for each requirement, the file and function(s) where it has to happen
  files         the files the Builder must see
  notes         conventions to follow

Day 3 showed why: on the cart job the Builder applied a discount code in summary() but never in shipping(),
three runs in a row. A list that says "FREESHIP makes shipping 0 → src/cart.js: shipping()" is the fix the
Checker then holds the work against. The Scout never writes files.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from team import llm
from wb_settings import Settings

PROMPT = (Path(__file__).resolve().parent / "prompts" / "scout.md").read_text(encoding="utf-8")
MAP_CHARS = 20000
OUTLINE_PER_FILE = 40

OUTLINE = {
    ".cs": re.compile(r"^\s*(?:\[.*\]\s*)?(?:(?:public|private|protected|internal|static|async|override|virtual|"
                      r"partial|sealed|abstract|readonly)\s+)*(?:class|record|interface|enum|struct)\s+\w+"
                      r"|^\s*(?:public|private|protected|internal)\s+[\w<>\[\],.?\s]*?\b\w+\s*\("
                      r"|\b\w+\.Map(?:Get|Post|Put|Delete|Patch|Group)\s*\(\s*\"[^\"]*\""
                      r"|^\s*\[(?:Http\w+|Route|Fact|Theory)\b"),
    ".js": re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\b\s*\*?\s*\w*"
                      r"|^\s*(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?(?:\(|function\b|\w+\s*=>)"
                      r"|^\s*(?:export\s+)?(?:const|let)\s+[A-Z_][A-Z0-9_]+\s*="
                      r"|^\s*(?:export\s+)?(?:default\s+)?class\s+\w+"
                      r"|^\s*(?:test|it|describe)\s*\(\s*['\"`]"
                      r"|\b(?:app|router)\.(?:get|post|put|delete|patch|use)\s*\(\s*['\"`]"),
    ".lua": re.compile(r"^\s*(?:local\s+)?function\s+[\w.:]+|^\s*[\w.]+\s*=\s*function\b"),
    ".py": re.compile(r"^\s*(?:async\s+)?def\s+\w+|^\s*class\s+\w+"),
    ".php": re.compile(r"^\s*(?:(?:public|private|protected|static|abstract|final)\s+)*function\s+\w+"
                       r"|^\s*(?:abstract\s+|final\s+)?class\s+\w+|Route::\w+\("),
    ".java": re.compile(r"^\s*(?:(?:public|private|protected|static|final|abstract)\s+)+[\w<>\[\],\s]*\b\w+\s*\("
                        r"|^\s*(?:public\s+)?(?:abstract\s+|final\s+)?(?:class|interface|enum)\s+\w+"),
}
for ext in (".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
    OUTLINE[ext] = OUTLINE[".js"]
OUTLINE[".kt"] = re.compile(r"^\s*(?:(?:private|public|internal|override|suspend|open)\s+)*fun\s+\w+"
                            r"|^\s*(?:(?:data|open|abstract|sealed)\s+)*class\s+\w+")


@dataclass
class ContextPack:
    requirements: list[str] = field(default_factory=list)
    places: list[dict] = field(default_factory=list)   # {"requirement": n, "file": ..., "where": ..., "change": ...}
    files: list[str] = field(default_factory=list)
    notes: str = ""
    problems: list[str] = field(default_factory=list)  # what was wrong with the Scout's answer
    raw: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.requirements and self.files)

    def for_prompt(self) -> str:
        out = []
        if self.requirements:
            out.append("Requirements (every one must be true when you are done):\n"
                       + "\n".join(f"R{i}. {r}" for i, r in enumerate(self.requirements, 1)))
        if self.places:
            out.append("Where each one happens, according to the Scout (check it; it can be wrong or incomplete):\n"
                       + "\n".join(f"- R{p.get('requirement', '?')} → {p.get('file', '?')}"
                                   + (f": {p['where']}" if p.get("where") else "")
                                   + (f" — {p['change']}" if p.get("change") else "") for p in self.places))
        if self.notes:
            out.append(f"Notes from the Scout: {self.notes}")
        return "\n\n".join(out)


def outline(repo: Path, rel: str) -> tuple[int, list[str]]:
    """(line count, "  12 export function shipping(cart) {" lines) for one file."""
    try:
        lines = (repo / rel).read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return 0, []
    pat = OUTLINE.get(Path(rel).suffix.lower())
    found = []
    if pat:
        for n, ln in enumerate(lines, 1):
            if pat.search(ln):
                found.append(f"  {n:>4} {ln.strip()[:110]}")
                if len(found) >= OUTLINE_PER_FILE:
                    found.append("       …")
                    break
    return len(lines), found


def repo_map(repo: Path, files: list[str], protected: set[str] = frozenset()) -> str:
    parts, total = [], 0
    for rel in files:
        count, found = outline(repo, rel)
        tag = "  (client's test, read only)" if rel in protected else ""
        block = f"{rel} ({count} lines){tag}" + ("\n" + "\n".join(found) if found else "")
        if total + len(block) > MAP_CHARS:
            parts.append(f"… and {len(files) - len(parts)} more files")
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts)


NAME = re.compile(r"\bfunction\s*\*?\s*(\w+)|\b(?:const|let|var)\s+(\w+)\s*=|\b(?:class|record|interface|struct|enum)\s+(\w+)"
                  r"|^\s*(?:local\s+)?function\s+(?:[\w.]+[.:])?(\w+)|\bdef\s+(\w+)|\bfun\s+(\w+)|(\w+)\s*\(")
NOT_NAMES = {"if", "for", "while", "switch", "catch", "return", "new", "function", "async", "await", "test", "it",
             "describe", "get", "post", "put", "delete", "patch", "use", "string", "void", "public", "private"}


def symbols(repo: Path, rel: str) -> list[tuple[str, int]]:
    """(name, line) of the functions, classes and constants a file defines (from its outline)."""
    out = []
    for line in outline(repo, rel)[1]:
        m = re.match(r"\s*(\d+)\s(.*)", line)
        if not m:
            continue
        n = NAME.search(m.group(2))
        name = next((g for g in n.groups() if g), None) if n else None
        if name and name.lower() not in NOT_NAMES and len(name) >= 4:
            out.append((name, int(m.group(1))))
    return out


def link_named(pack: "ContextPack", repo: Path, files: list[str]) -> None:
    """A requirement that names a function of the code ("FREESHIP makes shipping 0", and shipping() exists)
    gets that function as a place, unless the Scout already named it there. Models place a requirement where the
    task lists it (under applyCode) and miss the function whose result it changes: 0 of 9 Scout runs, three
    models, placed it right on Day 4. This finds it without a model. Test files are left out."""
    defs: dict[str, list[tuple[str, str, int]]] = {}
    for rel in files:
        if looks_like_test_file(rel):
            continue
        for name, line in symbols(repo, rel):
            if name.isupper() and len(name) > 1:
                continue   # SHIPPING the constant isn't the place; shipping() the function is
            defs.setdefault(name.lower(), []).append((name, rel, line))
    for i, req in enumerate(pack.requirements, 1):
        named = {w.lower() for w in re.findall(r"[A-Za-z_]\w{3,}", req)}
        mine = " ".join(str(p.get("where", "")) + " " + str(p.get("change", "")) for p in pack.places
                        if p.get("requirement") == i).lower()
        for w in sorted(named & defs.keys()):
            if re.search(rf"\b{re.escape(w)}\b", mine) or len(defs[w]) > 3:
                continue
            for original, rel, line in defs[w][:2]:
                pack.places.append({"requirement": i, "file": rel, "where": f"{original} (line {line})",
                                    "change": "named in this requirement: check whether it must change"})
                if rel not in pack.files:
                    pack.files.append(rel)


def looks_like_test_file(rel: str) -> bool:
    from team.edits import looks_like_test
    return looks_like_test(rel)


def _as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def parse(reply: str, files: list[str]) -> ContextPack:
    data = llm.first_json(reply)
    pack = ContextPack(raw=reply)
    if data is None:
        pack.problems.append("the Scout's answer had no JSON object")
        return pack
    known = set(files)
    lower = {f.lower(): f for f in files}
    by_name: dict[str, list[str]] = {}
    for f in files:
        by_name.setdefault(f.rsplit("/", 1)[-1].lower(), []).append(f)

    def real(path: str) -> str | None:
        p = str(path).strip().strip("`").replace("\\", "/")
        while p.startswith("./"):
            p = p[2:]
        if p in known:
            return p
        if p.lower() in lower:
            return lower[p.lower()]
        hits = by_name.get(p.rsplit("/", 1)[-1].lower(), [])
        return hits[0] if len(hits) == 1 else None

    pack.requirements = [str(r).strip() for r in _as_list(data.get("requirements")) if str(r).strip()]
    for item in _as_list(data.get("files")):
        path = item.get("path") if isinstance(item, dict) else item
        r = real(path or "")
        if r and r not in pack.files:
            pack.files.append(r)
        elif not r and path:
            new = isinstance(item, dict) and item.get("new")
            if not new:
                pack.problems.append(f"the Scout named {path}, which isn't in the repository")
    for p in _as_list(data.get("places")):
        if not isinstance(p, dict):
            continue
        r = real(p.get("file", ""))
        if r:
            p["file"] = r
            if r not in pack.files:
                pack.files.append(r)
        pack.places.append({k: p.get(k) for k in ("requirement", "file", "where", "change") if p.get(k) is not None})
    pack.notes = str(data.get("notes") or "").strip()
    if not pack.requirements:
        pack.problems.append("the Scout listed no requirements")
    return pack


def scout(s: Settings, repo: Path, step: str, files: list[str], protected: set[str], *, title: str = "",
          goal: str = "", stack: str = "web", stack_notes: str = "", request: str = "") -> ContextPack:
    """Ask the Scout model; tries once more if its answer can't be read. `request`: the job's own request, where
    the step's names and details come from."""
    system = PROMPT.replace("{stack}", stack).replace("{stack_notes}", stack_notes or "(none)")
    user = (f"Job: {title}\n" + (f"Goal: {goal}\n" if goal else "")
            + (f"\nFor reference only, the job's whole request (take names, numbers and details from it; the rest "
               f"of it is OTHER steps, so list requirements only for the step below):\n{request}\n" if request else "")
            + f"\nThe step (only this):\n{step}\n\n"
            f"Repository map (every file, with its classes, functions and endpoints by line number):\n\n"
            f"{repo_map(repo, files, protected)}")
    pack = ContextPack()
    for attempt in (1, 2):
        a = llm.ask(s, "scout", system, user if attempt == 1 else
                    user + "\n\nYour last answer couldn't be used (" + "; ".join(pack.problems)
                    + "). Answer with the JSON object only.", max_tokens=3000)
        pack = parse(a.text, files)
        if pack.ok:
            break
    link_named(pack, repo, files)
    return pack
