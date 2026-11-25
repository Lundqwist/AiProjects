"""Checker probes: small tests the Checker writes per requirement and runs, instead of only reading the code.

Day 5 showed the limit of reading: an 8B Checker read past `null` vs `undefined`, a bare `/junk keep`, a trailing
space. A probe runs the case. The probe writer gets the requirements, the client's own example test (so it uses the
same framework and helpers) and the names of the code's functions, not the Builder's code itself, so it works out
what should happen from the requirement and doesn't inherit the Builder's mistakes.

The probe file is placed next to the client's tests, named like them so their runner finds it (sell_test.lua →
checker_probe_test.lua, cart.test.js → checker-probe.test.js, ListTests.cs → CheckerProbeTests.cs), run through
the Runner's test action, and removed again: it is never committed. A copy is kept in the job's checker\\ folder.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

from team import edits, jobs, llm, runner, scout
from wb_settings import Settings

PROMPT = (Path(__file__).resolve().parent / "prompts" / "probe.md").read_text(encoding="utf-8")
HELPER_CHARS = 14000
NAMES = [  # example test name pattern → the probe's file name
    ("*_test.lua", "checker_probe_test.lua"), ("*_spec.lua", "checker_probe_spec.lua"),
    ("*.test.js", "checker-probe.test.js"), ("*.test.mjs", "checker-probe.test.mjs"),
    ("*.test.ts", "checker-probe.test.ts"), ("*.test.jsx", "checker-probe.test.jsx"),
    ("*.test.tsx", "checker-probe.test.tsx"), ("*.spec.js", "checker-probe.spec.js"),
    ("*.spec.ts", "checker-probe.spec.ts"), ("*Tests.cs", "CheckerProbeTests.cs"), ("*Test.cs", "CheckerProbeTest.cs"),
    ("test_*.py", "test_checker_probe.py"), ("*_test.py", "checker_probe_test.py"), ("*Test.java", "CheckerProbeTest.java"),
    ("*Test.kt", "CheckerProbeTest.kt"),
]
LANG = {".lua": "lua", ".js": "js", ".mjs": "js", ".ts": "ts", ".jsx": "jsx", ".tsx": "tsx", ".cs": "csharp",
        ".py": "python", ".java": "java", ".kt": "kotlin"}
FAIL_LINE = re.compile(r"^\s*(?:not ok\b|✖|Failed\b|FAIL\b|\[FAIL\])", re.I)
PASS_LINE = re.compile(r"^\s*(?:ok\b|✔|Passed\b|PASS\b)", re.I)
R_NUM = re.compile(r"(?<![A-Za-z0-9])R(\d{1,2})(?=[\s_:.\-)]|$)")


@dataclass
class ProbeRun:
    path: str = ""                      # relative, where it ran
    code: str = ""
    passed: list[int] = field(default_factory=list)     # requirement numbers
    failed: list[int] = field(default_factory=list)
    details: str = ""                   # the failing probes' lines from the run
    note: str = ""                      # why there are no usable probes, if so

    @property
    def usable(self) -> bool:
        return bool(self.passed or self.failed)


def place(client_files: list[str]) -> tuple[str, str] | None:
    """(the example test, the probe's path) from the client's own tests; None if it has none."""
    tests = [f for f in client_files if edits.looks_like_test(f)]
    for pattern, name in NAMES:
        for f in sorted(tests):
            if fnmatch.fnmatch(f.rsplit("/", 1)[-1], pattern):
                folder = f.rsplit("/", 1)[0] + "/" if "/" in f else ""
                return f, folder + name
    return None


def _helpers(repo: Path, example: str, client_files: list[str]) -> str:
    """The test folder's helper files (a harness, fixtures setup), so probes can use them as the example does."""
    folder = example.rsplit("/", 1)[0] if "/" in example else ""
    out, total = [], 0
    for f in sorted(client_files):
        if f == example or (f.rsplit("/", 1)[0] if "/" in f else "") != folder:
            continue
        if not f.endswith((".lua", ".js", ".mjs", ".ts", ".cs", ".py", ".json", ".csproj")):
            continue
        try:
            text = (repo / f).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if total + len(text) > HELPER_CHARS:
            continue
        out.append(f"--- {f} ---\n```\n{text.rstrip()}\n```")
        total += len(text)
    return "\n\n".join(out)


def _code_block(reply: str) -> str:
    m = re.search(r"```[\w#+-]*\s*\n(.*?)\n```", reply, re.S)
    return (m.group(1) if m else "").strip()


def outcomes(results: list[runner.Result]) -> tuple[list[int], list[int], str]:
    """(requirement numbers whose probes passed, those that failed, the failing lines with what follows them)."""
    passed, failed, details = set(), set(), []
    for r in results:
        if r.action != "test":
            continue
        lines = r.output.splitlines()
        for i, ln in enumerate(lines):
            m = R_NUM.search(ln)
            if not m:
                continue
            n = int(m.group(1))
            if FAIL_LINE.search(ln):
                failed.add(n)
                details += lines[i:i + 6] + [""]
            elif PASS_LINE.search(ln):
                passed.add(n)
    passed -= failed   # a requirement with one failing case fails
    return sorted(passed), sorted(failed), "\n".join(details).strip()


def write(s: Settings, job: jobs.Job, step: str, requirements: list[str], example: str, probe_path: str,
          error: str = "") -> str:
    repo = job.repo
    client = job.client_files()
    code_files = [f for f in builder_files(repo) if not edits.looks_like_test(f)]
    names = "\n".join(f"{f}:\n" + "\n".join(scout.outline(repo, f)[1]) for f in code_files[:12])
    ext = Path(probe_path).suffix
    user = (f"Step:\n{step}\n\nRequirements:\n" + "\n".join(f"R{i}. {r}" for i, r in enumerate(requirements, 1))
            + f"\n\nThe client's example test ({example}):\n```\n{(repo / example).read_text(encoding='utf-8')}\n```"
            + (f"\n\nHelpers in the same folder:\n\n{_helpers(repo, example, client)}" if _helpers(repo, example, client) else "")
            + f"\n\nThe code's functions, classes and endpoints (by line):\n{names}"
            + f"\n\nWrite the probe file {probe_path} ({LANG.get(ext, ext)}).")
    if error:
        user += f"\n\nYour last probe file didn't run:\n```\n{error}\n```\nFix it."
    a = llm.ask(s, "checker", PROMPT, user, max_tokens=16000, temperature=0.1)
    return _code_block(a.text)


def builder_files(repo: Path) -> list[str]:
    from team import builder   # (builder doesn't import this module)
    return builder.repo_files(repo)


def run(s: Settings, job: jobs.Job, step: str, requirements: list[str], *, code: str = "",
        ask_fn: runner.AskFn | None = None, round_no: int = 1) -> ProbeRun:
    """Write the probes (unless `code` is given: the same probes again in a later round), run them, remove them."""
    repo = job.repo
    spot = place(job.client_files())
    if not spot:
        return ProbeRun(note="the client's code has no tests to copy the style of, so no probes")
    example, rel = spot
    out = ProbeRun(path=rel)
    folder = job.folder / "checker"
    folder.mkdir(exist_ok=True)
    target = repo / rel
    if target.exists():
        return ProbeRun(note=f"{rel} already exists in the repository, so no probes")
    error = ""
    for attempt in (1, 2):
        out.code = code or write(s, job, step, requirements, example, rel, error)
        if not out.code:
            out.note = "the probe writer gave no code block"
            continue
        (folder / f"probe-round-{round_no}{Path(rel).suffix}").write_text(out.code + "\n", encoding="utf-8")
        try:
            target.write_text(out.code + "\n", encoding="utf-8")
            results = job.run("test", ask_fn=ask_fn)
        finally:
            target.unlink(missing_ok=True)
        out.passed, out.failed, out.details = outcomes(results)
        if out.usable:
            out.note = ""
            break
        error = "\n".join(e for r in results for e in (r.errors or r.output.splitlines()[-20:]))[:3000]
        out.note = "the probes didn't run (no test named R<n> was reported)"
        if code:
            break   # given code that doesn't run: don't rewrite it here
    job.log(f"Checker · probes · round {round_no}",
            (f"{rel}: passed R{', R'.join(map(str, out.passed))}" if out.passed else f"{rel}: none passed")
            + (f"\nfailed R{', R'.join(map(str, out.failed))}\n\n{out.details}" if out.failed else "")
            + (f"\n{out.note}" if out.note else ""))
    return out
