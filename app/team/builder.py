"""The Builder: makes one step of a job true in the code, and leaves it building with its tests passing.

One try = the code model writes SEARCH/REPLACE edits (team\\edits.py), the program applies them, and the
Runner builds and tests every project in the job's repository. When something fails, the next try gets
the current files, which edits were refused and why, and the Runner's error lines, with the failing tests
named as the client's or the Builder's own. At most 3 tries per step; then it stops and reports.

Until the Scout exists (Day 4), the Builder is shown every source file of the repository (up to a limit).
A step that passes is committed on the job's branch, so every step can be looked at or undone on its own.
Restores are ask-first (on screen); without an OK the step stops and says so.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import wb_ai
import wb_code
from team import edits, jobs, runner
from wb_settings import Settings

PROMPT = (Path(__file__).resolve().parent / "prompts" / "builder.md").read_text(encoding="utf-8")
CONTEXT_CHARS = 60000
FILE_CHARS = 20000
SKIP_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "composer.lock", "packages.lock.json"}
MAX_TRIES = 3
MAX_EDITS = 30   # more blocks than this in one reply: the model is repeating itself

Event = Callable[[str, str], None]   # (title, detail): progress for a screen or a terminal


@dataclass
class TryRecord:
    n: int
    seconds: float
    applied: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    checks: str = ""            # the Runner's report
    ok: bool = False


@dataclass
class BuildResult:
    status: str                 # pass, failed, ask
    step: str
    tries: list[TryRecord] = field(default_factory=list)
    message: str = ""           # for the person (and, later, the Checker)
    commit: str = ""
    new_files: list[str] = field(default_factory=list)
    disputes: list[str] = field(default_factory=list)   # FIX items the Builder says are wrong
    own_failing: list[str] = field(default_factory=list)  # its own test files that still fail (the Checker decides)
    changed_files: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "pass"


# ---- what the Builder sees ----------------------------------------------------------------------

def repo_files(repo: Path) -> list[str]:
    out = []
    for p in wb_code.project_files(repo):
        rel = p.relative_to(repo).as_posix()
        if p.name in SKIP_FILES or edits.is_secret(rel):
            continue
        out.append(rel)
    return out


def test_project_dirs(repo: Path) -> set[str]:
    """Folders of .NET test projects (relative), whose source files are tests."""
    dirs = set()
    for p in runner.find_projects(repo):
        for c in runner._csprojs(p) if p.kind == "dotnet" else []:
            if runner._is_test_project(runner.Project(p.pack, p.kind, c)):
                dirs.add(c.parent.relative_to(repo).as_posix())
    return dirs


SMALL_REPO = 30000  # a repository this small (characters of source) is shown whole, whatever the Scout picked


def shown_files(repo: Path, files: list[str], picked: list[str] | None) -> list[str]:
    """The Scout's files first; the rest too when the repository is small (a file the Scout missed is the most
    common way to fail, and a small repository costs little to show whole)."""
    if not picked:
        return files
    size = sum((repo / f).stat().st_size for f in files if (repo / f).exists())
    first = [f for f in picked if f in files]
    return first + [f for f in files if f not in first] if size <= SMALL_REPO else first


def context_pack(repo: Path, files: list[str], protected: set[str]) -> tuple[str, list[str]]:
    """The files as the Builder sees them, and notes about what was left out."""
    parts, total, notes = [], 0, []
    for rel in files:
        try:
            text = (repo / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
        except (UnicodeDecodeError, OSError):
            continue
        if len(text) > FILE_CHARS:
            text = text[:FILE_CHARS] + "\n…(cut here)"
            notes.append(f"{rel} was cut to {FILE_CHARS} characters")
        if total + len(text) > CONTEXT_CHARS:
            notes.append(f"{len(files) - len(parts)} files were left out (too much text)")
            break
        tag = "   (client's test, read only)" if rel in protected else ""
        parts.append(f"--- {rel}{tag} ---\n```\n{text.rstrip()}\n```")
        total += len(text)
    return "\n\n".join(parts), notes


# ---- whose tests fail ---------------------------------------------------------------------------

def _names(repo: Path, rel: str) -> set[str]:
    """What a test file can be recognised by in test output: its name, and the classes it defines."""
    p = Path(rel)
    names = {p.name, p.stem.split(".")[0]}
    try:
        names |= set(re.findall(r"\bclass\s+(\w+)", (repo / rel).read_text(encoding="utf-8", errors="replace")))
    except OSError:
        pass
    return {n for n in names if len(n) > 3}


def failing_owners(repo: Path, results: list[runner.Result], client: set[str], own: set[str]) -> tuple[list[str], list[str]]:
    """(client's test files, Builder's test files) that show up in the failed tests' error lines."""
    text = "\n".join("\n".join(r.errors) + "\n" + r.output for r in results if not r.ok and r.action == "test")
    if not text:
        return [], []
    hit = lambda rel: any(re.search(rf"(?<![\w.]){re.escape(n)}\b", text) for n in _names(repo, rel))  # noqa: E731
    return sorted(f for f in client if hit(f)), sorted(f for f in own if hit(f))


# ---- one step -----------------------------------------------------------------------------------

def _checks(job: jobs.Job, ask_fn, on_event: Event) -> tuple[list[runner.Result], str | None]:
    """Build every project, then test. Returns (results, stop reason) where a stop reason means the
    step can't go on without the person (a refused restore, a missing tool)."""
    results: list[runner.Result] = []
    for action in ("build", "test"):
        rs = job.run(action, ask_fn=ask_fn)
        if any(r.state == "needs restore" for r in rs):
            on_event("Restore needed", "asking on screen before downloading packages")
            restored = job.run("restore", ask_fn=ask_fn)
            refused = [r for r in restored if not r.ok]
            if refused:
                return results + restored, ("a restore (package download) is needed and wasn't allowed, or "
                                            "failed:\n" + runner.report(refused, "restore"))
            rs = job.run(action, ask_fn=ask_fn)
        missing = [r for r in rs if r.state == "not installed"]
        if missing:
            return results + rs, missing[0].errors[0]
        results += rs
        if not all(r.ok for r in rs):
            break   # no tests on code that doesn't build
    return results, None


def _feedback(outcomes: list[edits.Outcome], parsed: edits.Parsed, results: list[runner.Result],
              repo: Path, client_tests: set[str], own_tests: set[str]) -> str:
    out = []
    if parsed.problems:
        out.append("Parts of your reply couldn't be read:\n" + "\n".join(f"- {p}" for p in parsed.problems))
    if outcomes:
        out.append("Your edits:\n" + edits.report(outcomes))
    elif not parsed.problems:
        out.append("Your reply had no === EDIT: or === FILE: blocks, so nothing was changed.")
    if results:
        failed = [r for r in results if not r.ok]
        if failed:
            out.append("The checks after your edits:\n" + runner.report(results, "build and test"))
            c, o = failing_owners(repo, results, client_tests, own_tests)
            if c:
                out.append("Failing tests in the client's files (read only; the code must make them pass): "
                           + ", ".join(c))
            if o:
                out.append("Failing tests in files you wrote: " + ", ".join(o) + ". Decide whether the test or "
                           "the code is wrong; a test of yours that expects something the step didn't ask for "
                           "is wrong.")
    return "\n\n".join(out)


def build_step(job: jobs.Job, step: str, s: Settings | None = None, *, ask_fn: runner.AskFn | None = None,
               fix_list: str = "", max_tries: int = MAX_TRIES, on_event: Event | None = None,
               commit: bool = True, pack=None, commit_message: str = "", allow_disputes: bool = True) -> BuildResult:
    """Make `step` true in the job's repository. `fix_list` = what a review found wrong last time.
    `pack` = the Scout's ContextPack (requirements, places, files), when there is one."""
    s = s or Settings.load()
    on_event = on_event or (lambda t, d: None)
    repo = job.repo.resolve()
    card = job.card()
    stack = (card.stack or ", ".join(runner.detect(repo)) or "web").split(",")[0].strip()
    notes = runner.pack_notes(stack) or "(none)"
    system = PROMPT.replace("{stack}", stack).replace("{stack_notes}", notes)
    start_files = set(repo_files(repo))
    # the client's tests are those in the code as received; tests from earlier steps are the Builder's own
    received = set(job.client_files())
    client_tests = edits.protected_tests(repo, sorted(received), test_project_dirs(repo))
    replies = job.folder / "builder"
    replies.mkdir(exist_ok=True)
    step_no = len(list(replies.glob("step-*-try-1.md"))) + 1
    job.log(f"Builder · step {step_no} · {step[:80]}",
            f"model {wb_ai.expert_model(s, 'code')}\nclient's tests (read only): "
            + (", ".join(sorted(client_tests)) or "none"))

    result = BuildResult("failed", step)
    feedback = f"A review of the last attempt found:\n{fix_list}" if fix_list else ""
    ever_applied = False
    for n in range(1, max_tries + 1):
        files = repo_files(repo)
        shown = shown_files(repo, files, pack.files if pack else None)
        text, ctx_notes = context_pack(repo, shown, client_tests)
        hidden = [f for f in files if f not in shown]
        if hidden:
            ctx_notes.append("not shown (the Scout didn't pick them): " + ", ".join(hidden[:40]))
        own_tests = {f for f in files if f not in received and edits.looks_like_test(f, test_project_dirs(repo))}
        user = (f"Job: {card.title}\n" + (f"Goal: {card.goal}\n" if card.goal else "")
                + f"\nStep:\n{step}\n"
                + (f"\n{pack.for_prompt()}\n" if pack and pack.requirements else "")
                + f"\nThe repository's files (current state):\n\n{text}"
                + (f"\n\n({'; '.join(ctx_notes)})" if ctx_notes else "")
                + (f"\n\nThis is try {n} of {max_tries}. What happened on the last try:\n\n{feedback}" if feedback else ""))
        on_event(f"Try {n} of {max_tries}", f"asking {wb_ai.expert_model(s, 'code')} for edits")
        started = time.monotonic()
        try:
            a = wb_ai.ask(s, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                          task="code", expert="code", big=False, temperature=0.2, max_tokens=8000)
            reply = a.text
        except Exception as e:  # noqa: BLE001 - a model crash is a failed try, written to the log
            reply = ""
            job.log(f"Builder · try {n} · the model failed", f"{type(e).__name__}: {e}")
        seconds = time.monotonic() - started
        (replies / f"step-{step_no}-try-{n}.md").write_text(reply, encoding="utf-8")
        rec = TryRecord(n, round(seconds))
        result.tries.append(rec)

        parsed = edits.parse(reply)
        if len(parsed.edits) > MAX_EDITS:
            parsed.problems.append(f"the reply had {len(parsed.edits)} edit blocks, so it ran away (repeated "
                                   "itself) and nothing was applied. Write each change once, in a few blocks")
            parsed.edits = []
        if parsed.disputes and not allow_disputes:
            parsed.problems.append("DISPUTE isn't accepted in this round: the Checker has read your dispute and "
                                   "still finds the problem. Change the code")
            parsed.disputes = []
        if parsed.needs_approval:
            result.status = "ask"
            result.message = "The Builder needs a new package, which needs your OK:\n" + "\n".join(
                f"- {x}" for x in parsed.needs_approval)
            job.log(f"Builder · try {n} · asks for approval", result.message)
            break
        outcomes = edits.apply(repo, parsed.edits, client_tests)
        rec.applied = [o.edit.path for o in outcomes if o.ok]
        rec.refused = [f"{o.edit.path}: {o.why.splitlines()[0]}" for o in outcomes if not o.ok]
        ever_applied |= bool(rec.applied)
        if fix_list and parsed.disputes:
            result.disputes = parsed.disputes   # the last try's disputes go to the Checker's next round
        # in a FIX round, disputing every item (no edits) is a real answer: the Checker decides on it next
        answered = ever_applied or bool(fix_list and parsed.disputes)
        on_event(f"Try {n}: {len(rec.applied)} edits applied, {len(rec.refused)} refused"
                 + (f", {len(parsed.disputes)} disputed" if parsed.disputes else ""), "building and testing")
        results, stop = _checks(job, ask_fn, on_event) if answered else ([], None)
        rec.checks = runner.report(results, "build and test") if results else ""
        job.log(f"Builder · try {n} · {seconds:.0f} s · {len(rec.applied)} applied, {len(rec.refused)} refused"
                + (f", {len(parsed.disputes)} disputed" if parsed.disputes else ""),
                edits.report(outcomes) + "".join(f"\nDISPUTE: {d}" for d in parsed.disputes)
                + (f"\n\n{rec.checks}" if rec.checks else ""))
        if stop:
            result.status, result.message = "ask", stop
            break
        unfinished = edits.placeholders(outcomes)
        # a refused edit is a change the Builder meant and didn't get: no pass on top of it (Day 4: a refused fix
        # plus two disputes counted as done). Refusing an edit to the client's tests is meant, and doesn't count.
        missed = [o for o in outcomes if not o.ok and o.edit.path not in client_tests]
        rec.ok = (answered and bool(results) and all(r.ok for r in results) and not unfinished and not missed
                  and not (len(outcomes) == 0 and parsed.problems and not parsed.disputes))
        if rec.ok:
            result.status = "pass"
            break
        # Day 1 lesson, seen again on Day 5: the Builder's own new test expects the wrong thing (1200 copper as
        # "1s 20c"), and it bends correct code to fit, or never passes. After one try to sort it out itself, a
        # step where everything builds and only its own tests fail goes to the Checker, which decides whether the
        # test or the code is wrong.
        if answered and results and not unfinished and not missed and n >= 2:
            builds_ok = all(r.ok for r in results if r.action == "build")
            c, o = failing_owners(repo, results, client_tests, own_tests)
            if builds_ok and o and not c:
                result.status, result.own_failing = "pass", o
                job.log(f"Builder · try {n} · only its own tests fail: the Checker decides", ", ".join(o))
                break
        feedback = _feedback(outcomes, parsed, results, repo, client_tests, own_tests)
        if unfinished:
            feedback += ("\n\nYour edits left work undone (placeholder lines). Write the real code instead:\n"
                         + "\n".join(f"- {u}" for u in unfinished))
            job.log(f"Builder · try {n} · placeholders", "\n".join(unfinished))

    now = set(repo_files(repo))
    result.new_files = sorted(now - start_files)
    changed = (jobs.git(repo, "diff", "--name-only", "HEAD", check=False) + "\n"
               + jobs.git(repo, "ls-files", "--others", "--exclude-standard", check=False))
    result.changed_files = sorted({ln.strip() for ln in changed.splitlines() if ln.strip()})
    if result.ok:
        result.message = (f"Step done on try {len(result.tries)}: it builds and the tests pass.\n"
                          if not result.own_failing else
                          f"Step built on try {len(result.tries)}; only tests the Builder wrote fail "
                          f"({', '.join(result.own_failing)}): the Checker decides whether the test or the code is "
                          "wrong.\n") + result.tries[-1].checks
        if commit and result.changed_files:
            jobs.git(repo, "add", "-A")
            jobs.git(repo, "-c", "user.name=AI Workbench Builder", "-c", "user.email=workbench@localhost",
                     "commit", "--quiet", "-m", commit_message or f"Builder: {step.splitlines()[0][:72]}")
            result.commit = jobs.git(repo, "rev-parse", "--short", "HEAD")
    elif result.status == "failed":
        last = result.tries[-1] if result.tries else None
        result.message = (f"Stopped after {len(result.tries)} tries without a passing build and tests. "
                          "The changes are left in the repository (not committed) for you or the next round.\n"
                          + ((last.checks or "\n".join(last.refused)) if last else ""))
    job.log(f"Builder · step {step_no} · {result.status}" + (f" · commit {result.commit}" if result.commit else ""),
            result.message)
    return result
