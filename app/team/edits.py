"""The Builder's edits: SEARCH/REPLACE blocks for existing files, whole files for new ones.

    === EDIT: src/cart.js ===
    <<<<<<< SEARCH
    exact existing lines
    =======
    the new lines
    >>>>>>> REPLACE

    === FILE: src/new.js ===
    ```js
    whole file
    ```

Whole-file rewrites were where the local models lost code (Day 1): an edit names only the lines it
changes, so the rest of the file can't be dropped by accident. An edit whose SEARCH text isn't in the
file exactly once is refused, and the Builder is shown the file's real lines nearest to what it wrote.

Refused always: paths outside the repository, .git, secrets (.env, keys, certificates), and the
client's existing tests (new tests go in new files; see protected_tests).
"""
from __future__ import annotations

import difflib
import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

EDIT_HEAD = re.compile(r"^\s*(?:=+|#{1,6})\s*\**EDIT\s*:\s*\**\s*`?([^`=*]+?)`?\**\s*=*\s*$", re.I)
FILE_HEAD = re.compile(r"^\s*(?:=+|#{1,6})\s*\**FILE\s*:\s*\**\s*`?([^`=*]+?)`?\**\s*=*\s*$", re.I)
SEARCH = re.compile(r"^\s*<{5,}\s*SEARCH\s*$")
DIVIDER = re.compile(r"^\s*={5,}\s*$")
REPLACE = re.compile(r"^\s*>{5,}\s*REPLACE\s*$")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
RUN = re.compile(r"^\s*RUN\s*:\s*(\w+)\s*$", re.I | re.M)
NEEDS = re.compile(r"^\s*NEEDS_APPROVAL\s*:\s*(.+)$", re.I | re.M)

SECRET_FILES = (".env", ".env.*", "*.pem", "*.key", "*.pfx", "*.p12", "*.snk", "id_rsa*", "id_ed25519*",
                "secrets.json", "*.keystore", "*.jks", "credentials*", "*.author.p12")
# The client's tests: files in a test project or test folder, and files named like tests
TEST_DIRS = {"test", "tests", "__tests__", "spec", "specs", "e2e"}
TEST_NAMES = ("*.test.*", "*.spec.*", "*Tests.cs", "*Test.cs", "*_test.*", "test_*.*", "*Test.java", "*_spec.lua")


@dataclass
class Edit:
    path: str
    search: str           # "" for a new file
    replace: str
    new_file: bool = False


@dataclass
class Parsed:
    edits: list[Edit] = field(default_factory=list)
    run: list[str] = field(default_factory=list)        # actions asked for with RUN:
    needs_approval: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)   # blocks that couldn't be read


@dataclass
class Outcome:
    edit: Edit
    ok: bool
    why: str = ""        # for a refusal: the reason, with the file's nearest real lines when SEARCH missed


def _strip_fence(lines: list[str]) -> list[str]:
    """Models often wrap SEARCH/REPLACE in ```: drop a fence line at the start and end."""
    if lines and FENCE.match(lines[0]):
        lines = lines[1:]
    if lines and FENCE.match(lines[-1]) and FENCE.match(lines[-1]).group(0).strip() == lines[-1].strip():
        lines = lines[:-1]
    return lines


def parse(reply: str) -> Parsed:
    out = Parsed()
    lines = reply.replace("\r\n", "\n").split("\n")
    i, current = 0, None   # current = the path of the last === EDIT: header
    while i < len(lines):
        line = lines[i]
        m_edit, m_file = EDIT_HEAD.match(line), FILE_HEAD.match(line)
        if m_edit:
            current = m_edit.group(1).strip().replace("\\", "/")
            i += 1
            continue
        if m_file:
            path = m_file.group(1).strip().replace("\\", "/")
            current = None
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j >= len(lines) or not FENCE.match(lines[j]):
                out.problems.append(f"=== FILE: {path} === has no ``` code block after it")
                i += 1
                continue
            fence = FENCE.match(lines[j]).group(1)
            k = j + 1
            while k < len(lines) and lines[k].strip() != fence:
                k += 1
            if k >= len(lines):
                out.problems.append(f"{path}: its code block never closes (the reply was cut off?)")
                break
            out.edits.append(Edit(path, "", "\n".join(lines[j + 1:k]) + "\n", new_file=True))
            i = k + 1
            continue
        if SEARCH.match(line):
            j = i + 1
            while j < len(lines) and not DIVIDER.match(lines[j]) and not SEARCH.match(lines[j]):
                j += 1
            k = j + 1
            while k < len(lines) and not REPLACE.match(lines[k]) and not SEARCH.match(lines[k]):
                k += 1
            if j >= len(lines) or k >= len(lines) or not DIVIDER.match(lines[j]) or not REPLACE.match(lines[k]):
                out.problems.append(f"a SEARCH block{f' for {current}' if current else ''} has no ======= or "
                                    ">>>>>>> REPLACE line")
                i = j if j > i + 1 else i + 1
                continue
            if current is None:
                out.problems.append("a SEARCH/REPLACE block has no === EDIT: path === line before it")
            else:
                search = _strip_fence(lines[i + 1:j])
                replace = _strip_fence(lines[j + 1:k])
                out.edits.append(Edit(current, "\n".join(search), "\n".join(replace)))
            i = k + 1
            continue
        i += 1
    out.run = [a.lower() for a in RUN.findall(reply)]
    out.needs_approval = [n.strip() for n in NEEDS.findall(reply)]
    return out


# ---- what may be changed ------------------------------------------------------------------------

def _inside(path: Path, root: Path) -> bool:
    path, root = path.resolve(), root.resolve()
    return path == root or root in path.parents


def is_secret(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1].lower()
    return any(fnmatch.fnmatch(name, p) for p in SECRET_FILES)


def looks_like_test(rel: str, test_project_dirs: set[str] = frozenset()) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    if any(p.lower() in TEST_DIRS for p in parts[:-1]):
        return True
    if any(rel.startswith(d.rstrip("/") + "/") for d in test_project_dirs if d):
        return True
    return any(fnmatch.fnmatch(name, p) for p in TEST_NAMES)


def protected_tests(repo: Path, files: list[str], test_project_dirs: set[str]) -> set[str]:
    """The client's existing tests: test-looking files that are there before the Builder starts.
    Source files of a test project count too, but not its project file (adding a package reference
    is an ask-first action anyway, and the Builder may need to add a test file to it)."""
    return {f for f in files if looks_like_test(f, test_project_dirs) and not f.endswith((".csproj", ".json"))}


# ---- applying -----------------------------------------------------------------------------------

def _read(p: Path) -> tuple[str, str, bool]:
    """(text with \\n line ends, the file's own line end, had a BOM)."""
    raw = p.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:].decode("utf-8") if bom else raw.decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), nl, bom


def _write(p: Path, text: str, nl: str, bom: bool) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    data = text.replace("\n", nl).encode("utf-8")
    p.write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)


def _find(text: str, search: str) -> tuple[int, int] | None | str:
    """(start, end) of the one place `search` is in `text`. Exact first; then line by line ignoring
    trailing spaces; then ignoring indentation too (the replacement is re-indented to match).
    Returns "many" when it fits more than one place, None when it fits none."""
    n = text.count(search)
    if n == 1:
        i = text.index(search)
        return i, i + len(search)
    if n > 1:
        return "many"
    lines = text.split("\n")
    want = search.split("\n")
    while want and not want[-1].strip():
        want.pop()
    while want and not want[0].strip():
        want.pop(0)
    if not want:
        return None
    for norm in (str.rstrip, str.strip):
        w = [norm(x) for x in want]
        hits = [s for s in range(len(lines) - len(w) + 1) if [norm(x) for x in lines[s:s + len(w)]] == w]
        if len(hits) > 1:
            return "many"
        if hits:
            s = hits[0]
            start = sum(len(x) + 1 for x in lines[:s])
            end = start + sum(len(x) + 1 for x in lines[s:s + len(w)]) - 1
            return start, end
    return None


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _reindent(replace: str, search: str, found: str) -> str:
    """When SEARCH matched only with other indentation, shift the replacement the same way."""
    s_first = next((x for x in search.split("\n") if x.strip()), "")
    f_first = next((x for x in found.split("\n") if x.strip()), "")
    have, want = _indent(s_first), _indent(f_first)
    if have == want:
        return replace
    out = []
    for x in replace.split("\n"):
        if x.startswith(have):
            out.append(want + x[len(have):])
        elif x.strip():
            out.append(want + x.lstrip())
        else:
            out.append(x)
    return "\n".join(out)


def nearest(text: str, search: str, context: int = 3) -> str:
    """The file's real lines closest to a SEARCH that didn't match, so the Builder can copy them."""
    lines = text.split("\n")
    want = [x.strip() for x in search.split("\n") if x.strip()]
    if not want or not lines:
        return ""
    size = len(want)
    best, best_at = 0.0, 0
    for s in range(max(1, len(lines) - size + 1)):
        got = [x.strip() for x in lines[s:s + size]]
        r = difflib.SequenceMatcher(None, "\n".join(want), "\n".join(got)).ratio()
        if r > best:
            best, best_at = r, s
    a, b = max(0, best_at - context), min(len(lines), best_at + size + context)
    return "\n".join(f"{n + 1:>4}| {lines[n]}" for n in range(a, b))


def apply(repo: Path, edits: list[Edit], protected: set[str] = frozenset()) -> list[Outcome]:
    """Apply the edits in order. Each one either applies whole or is refused with a reason; the
    others still apply. Line ends, BOM and encoding of each file are kept."""
    repo = Path(repo).resolve()
    out: list[Outcome] = []
    for e in edits:
        rel = e.path.strip().replace("\\", "/")
        while rel.startswith("./"):
            rel = rel[2:]
        target = repo / rel

        def refuse(why: str) -> None:
            out.append(Outcome(e, False, why))

        if not rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel) or not _inside(target, repo):
            refuse("the path is outside the job's repository")
            continue
        if ".git" in rel.split("/"):
            refuse(".git is never changed")
            continue
        if is_secret(rel):
            refuse("secret files (.env, keys, certificates) are never changed")
            continue
        if rel in protected:
            refuse("this is one of the client's existing tests: don't change it. If the code is right and you "
                   "need more tests, put them in a new file")
            continue
        if e.new_file:
            if target.exists():
                # a "new file" for an existing file is a whole-file rewrite: only for small files
                try:
                    old, nl, bom = _read(target)
                except UnicodeDecodeError:
                    refuse("the file exists and isn't text")
                    continue
                if len(old.split("\n")) > 60:
                    refuse(f"{rel} already exists ({len(old.splitlines())} lines): change it with === EDIT: "
                           "blocks, not a whole new file")
                    continue
                _write(target, e.replace, nl, bom)
            else:
                _write(target, e.replace, "\r\n" if _repo_uses_crlf(repo) else "\n", False)
            out.append(Outcome(e, True))
            continue
        if not target.exists():
            refuse(f"{rel} doesn't exist: to create it, write it whole with === FILE: {rel} ===")
            continue
        try:
            text, nl, bom = _read(target)
        except UnicodeDecodeError:
            refuse("the file isn't UTF-8 text")
            continue
        if not e.search.strip():
            refuse("the SEARCH part is empty: copy the existing lines the change goes after or replaces")
            continue
        where = _find(text, e.search)
        if where == "many":
            refuse("the SEARCH text fits more than one place in the file: add a line or two around it so "
                   "it fits only one")
            continue
        if where is None:
            near = nearest(text, e.search)
            refuse("the SEARCH text isn't in the file (it must be copied exactly)."
                   + (f" The file's closest lines are:\n{near}" if near else ""))
            continue
        a, b = where
        found = text[a:b]
        replace = e.replace if found == e.search else _reindent(e.replace, e.search, found)
        _write(target, text[:a] + replace + text[b:], nl, bom)
        out.append(Outcome(e, True))
    return out


def _repo_uses_crlf(repo: Path) -> bool:
    """New files get the line ends the repository's other source files have."""
    from team.runner import _walk
    for here, files in _walk(repo):
        for f in files:
            if Path(f).suffix.lower() in (".cs", ".js", ".jsx", ".ts", ".tsx", ".lua", ".java", ".kt", ".php"):
                try:
                    return b"\r\n" in (here / f).read_bytes()[:4000]
                except OSError:
                    continue
    return False


PLACEHOLDER = re.compile(
    r"in a real (app|application|world|implementation|project|system)|for (this|the) (exercise|example|demo)\b"
    r"|(?-i:\bTODO\b|\bFIXME\b)|NotImplementedException|not implemented|\bplaceholder (code|logic|implementation)"
    r"|would (normally|go here)|implement (this|me|later)|left as an exercise", re.I)  # TODO in capitals only:
# lower case "todo" is a variable name in every todo app


def placeholders(outcomes: list[Outcome]) -> list[str]:
    """New lines in applied edits that say the work isn't really done ("in a real app we'd remove it…").
    The model wrote one over a DELETE that removed nothing (Day 3): it built and the client's tests passed."""
    found = []
    for o in outcomes:
        if not o.ok or o.edit.path.lower().endswith((".md", ".markdown", ".txt")):
            continue
        old = set(o.edit.search.split("\n"))
        for line in o.edit.replace.split("\n"):
            if line not in old and PLACEHOLDER.search(line):
                found.append(f"{o.edit.path}: {line.strip()}")
    return found


def report(outcomes: list[Outcome]) -> str:
    """For the Builder's next try: which edits applied and why the others were refused."""
    lines = []
    for o in outcomes:
        what = "new file" if o.edit.new_file else "edit"
        if o.ok:
            lines.append(f"- applied: {what} {o.edit.path}")
        else:
            lines.append(f"- REFUSED: {what} {o.edit.path}: {o.why}")
    return "\n".join(lines)
