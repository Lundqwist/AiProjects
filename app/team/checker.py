"""The Checker: judges one finished step, as a different model from the Builder. It never edits code.

The program runs the build and tests itself first (the Builder's word isn't taken for it): if they fail, the
verdict is FIX with the errors, without asking a model. Otherwise the Checker model gets the job, the step, the
Scout's requirements, the diff of the step and the changed files whole (an omission, such as a function that
should have changed and didn't, can't be seen in a diff alone), and answers requirement by requirement:

    R1: DONE - src/cart.js:41 rounds every amount
    R3: MISSING - shipping() ignores the code, so FREESHIP still charges 49
    VERDICT: FIX
    FIX list:
    1. src/cart.js:30 - shipping() doesn't look at cart.code - return 0 when the code is FREESHIP

A PASS with a requirement marked MISSING or WRONG counts as FIX: the program holds the model to its own list.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from team import edits, jobs, llm, runner  # noqa: F401 (edits: tests and the client's files)
from wb_settings import Settings

PROMPT = (Path(__file__).resolve().parent / "prompts" / "checker.md").read_text(encoding="utf-8")
FILES_CHARS = 30000
MAX_FIX = 15
DIFF_CHARS = 20000

REQ_LINE = re.compile(r"^\s*\**\s*R(\d+)\s*\**\s*[:.)-]\s*\**\s*(DONE|MISSING|WRONG|PARTIAL|UNCLEAR)\**\s*[-:–—]?\s*(.*)$",
                      re.I | re.M)
# "VERDICT: PASS | FIX | ASK" is the template copied, not a verdict (Day 4): the line must hold one word only
VERDICT = re.compile(r"^\s*\**\s*VERDICT\s*\**\s*:\s*\**\s*(PASS|FIX|ASK)\b\**\s*\.?\s*$", re.I | re.M)
FIX_ITEM = re.compile(r"^\s*(\d+)[.)]\s+(.+)$", re.M)
PROBE_LINE = re.compile(r"^\s*\**\s*P(\d+)\s*\**\s*[:.)-]\s*\**\s*(CODE[- ]WRONG|PROBE[- ]WRONG)\**\s*[-:–—]?\s*(.*)$",
                        re.I | re.M)
ASK_LINE = re.compile(r"^\s*\**\s*ASK\s*\**\s*(?:\(.*?\))?\s*:\s*(.+)$", re.I | re.M)


@dataclass
class Verdict:
    verdict: str                     # PASS, FIX, ASK
    reasons: list[str] = field(default_factory=list)
    fix_list: list[str] = field(default_factory=list)
    question: str = ""
    requirements: dict[int, tuple[str, str]] = field(default_factory=dict)  # n: (state, note)
    checks: str = ""                 # the program's own build/test report
    raw: str = ""
    by: str = ""                     # "the checks" or the model
    probe_code: str = ""             # the Checker's probes, when some of them show the code is wrong
    probe_details: str = ""

    def fix_text(self) -> str:
        """What the Builder gets for its next round."""
        out = [f"The Checker ({self.by}) found:"]
        out += [f"{i}. {f}" for i, f in enumerate(self.fix_list, 1)] or [f"- {r}" for r in self.reasons]
        if self.checks and self.by == "the checks":
            out.append("\n" + self.checks)
        if self.probe_code:
            out.append("\nThe Checker's probe tests (they are not in the repository; they show the failing cases):\n"
                       f"```\n{self.probe_code}\n```\nWhat failed when they ran:\n```\n{self.probe_details}\n```")
        return "\n".join(out)


def step_diff(repo: Path, since: str) -> str:
    d = jobs.git(repo, "diff", "--no-color", "--unified=3", f"{since}..HEAD", check=False)
    return d if len(d) <= DIFF_CHARS else d[:DIFF_CHARS] + "\n…(diff cut here)"


def changed_files(repo: Path, since: str) -> list[str]:
    return [f for f in jobs.git(repo, "diff", "--name-only", f"{since}..HEAD", check=False).splitlines() if f.strip()]


def files_text(repo: Path, files: list[str]) -> str:
    """Changed files whole, code before tests, with line numbers (the FIX list points at lines)."""
    order = sorted(files, key=lambda f: (edits.looks_like_test(f), f))
    parts, total = [], 0
    for rel in order:
        p = repo / rel
        if not p.exists():
            parts.append(f"--- {rel} (deleted) ---")
            continue
        try:
            lines = p.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
        except (UnicodeDecodeError, OSError):
            continue
        body = "\n".join(f"{n:>4}| {ln}" for n, ln in enumerate(lines, 1))
        if total + len(body) > FILES_CHARS:
            parts.append(f"--- {rel} (left out: too long) ---")
            continue
        parts.append(f"--- {rel} ---\n{body}")
        total += len(body)
    return "\n\n".join(parts)


def parse(reply: str, n_requirements: int) -> Verdict:
    text = re.sub(r"<think>.*?</think>", "", reply, flags=re.S)
    m = VERDICT.search(text)
    v = Verdict(m.group(1).upper() if m else "", raw=reply)
    for rm in REQ_LINE.finditer(text):
        v.requirements[int(rm.group(1))] = (rm.group(2).upper(), rm.group(3).strip())
    after = text[text.lower().find("fix list"):] if "fix list" in text.lower() else ""
    if after:
        stop = re.search(r"^\s*\**\s*ASK\b", after, re.I | re.M)
        items = [i.group(2).strip() for i in FIX_ITEM.finditer(after[:stop.start()] if stop else after)]
        # a model that starts repeating itself lists the same item hundreds of times (337, Day 5b); items that
        # say no change is needed aren't items
        items = [i for i in dict.fromkeys(items) if not re.search(r"no change (is )?needed|already correct", i, re.I)]
        v.fix_list = items[:MAX_FIX]
    am = ASK_LINE.search(text)
    if am and am.group(1).strip().lower() not in ("none", "-", "n/a"):
        v.question = am.group(1).strip()
    bad = {n: note for n, (state, note) in v.requirements.items() if state != "DONE"}
    if v.verdict == "PASS" and bad:           # held to its own list
        v.verdict = "FIX"
        v.reasons.append("the Checker said PASS but marked requirements as not done")
    if v.verdict == "" and bad:               # a requirement not done is a FIX, verdict line or not
        v.verdict = "FIX"
    if v.verdict == "FIX" and not v.fix_list:
        v.fix_list = [f"R{n}: {note or 'not done'}" for n, note in sorted(bad.items())]
    if v.verdict == "" and n_requirements and len(v.requirements) >= n_requirements and not bad:
        v.verdict = "PASS"                    # every requirement DONE, verdict line forgotten
    if v.verdict == "ASK" and not v.question:
        v.question = "The Checker wants your view but didn't say on what; see its answer in the job folder."
    v.reasons += [f"R{n} {s}: {note}" for n, (s, note) in sorted(v.requirements.items()) if s != "DONE"]
    return v


def check(s: Settings, job: jobs.Job, step: str, requirements: list[str], since: str, *,
          ask_fn: runner.AskFn | None = None, round_no: int = 1, last_fix: list[str] | None = None,
          disputes: list[str] | None = None, probe_state: dict | None = None) -> Verdict:
    """`last_fix` = this Checker's FIX list from the last round; `disputes` = the items the Builder says were
    wrong, with its reasons. The Checker reads the code again for those and decides."""
    repo = job.repo.resolve()
    results = job.run("build", ask_fn=ask_fn)
    if all(r.ok for r in results):
        results += job.run("test", ask_fn=ask_fn)
    report = runner.report(results, "build and test") if results else "No project has a build or test action."
    own_failing: list[str] = []
    if results and not all(r.ok for r in results):
        from team import builder   # (builder doesn't import the checker)
        received = set(job.client_files())
        client_tests = edits.protected_tests(repo, sorted(received), builder.test_project_dirs(repo))
        own_tests = {f for f in builder.repo_files(repo) if f not in received
                     and edits.looks_like_test(f, builder.test_project_dirs(repo))}
        c, own_failing = builder.failing_owners(repo, results, client_tests, own_tests)
        builds_ok = all(r.ok for r in results if r.action == "build")
        if not (builds_ok and own_failing and not c):
            v = Verdict("FIX", ["the build or tests fail when the Checker runs them"], checks=report, by="the checks")
            job.log(f"Checker · round {round_no} · FIX (the checks fail)", report)
            return v
        # only the Builder's own tests fail: the model decides which is wrong, the test or the code
    card = job.card()
    stack = (card.stack or ", ".join(runner.detect(repo)) or "web").split(",")[0].strip()
    notes = runner.pack_notes(stack)
    files = changed_files(repo, since)
    reqs = "\n".join(f"R{i}. {r}" for i, r in enumerate(requirements, 1)) or "(none listed: judge against the step)"
    user = (f"Job: {card.title}\n" + (f"Goal: {card.goal}\n" if card.goal else "") + f"\nStep:\n{step}\n\n"
            f"Requirements:\n{reqs}\n\n"
            + (f"Facts about this stack (judge the code by these):\n{notes}\n\n" if notes else "")
            + f"Your own run of the build and tests:\n{report}\n\n"
            f"The step's diff:\n```diff\n{step_diff(repo, since) or '(no changes)'}\n```\n\n"
            f"The changed files as they are now (line numbers on the left):\n\n{files_text(repo, files)}")
    probe = None
    probe_state = probe_state if probe_state is not None else {}
    if getattr(s, "team_probes", True) and requirements:
        from team import probes
        probe = probes.run(s, job, step, requirements, code=probe_state.get("code", ""), ask_fn=ask_fn,
                           round_no=round_no)
        if probe.usable:
            probe_state["code"] = probe.code
            wrong = probe_state.setdefault("wrong", set())
            probe.failed = [n for n in probe.failed if n not in wrong]
            user += (f"\n\nYour probe tests ({probe.path}, written from the requirements only, then run):\n"
                     f"```\n{probe.code}\n```\nResults: passed "
                     + (", ".join(f"R{n}" for n in probe.passed) or "none") + "; failed "
                     + (", ".join(f"R{n}" for n in probe.failed) or "none")
                     + (f"\n```\n{probe.details}\n```" if probe.failed else "")
                     + ("\nFor EVERY failing probe, add one line before the VERDICT:\n"
                        "P<n>: CODE-WRONG - <what the code does wrong for this case>\n"
                        "or P<n>: PROBE-WRONG - <why the probe expects something the requirement doesn't say>\n"
                        "Read the requirement again before you call a probe wrong. A requirement whose probe is "
                        "CODE-WRONG is MISSING or WRONG." if probe.failed else
                        "\nEvery probe passed; still read the code for what the probes don't cover."))
        elif probe.note:
            job.log(f"Checker · probes · round {round_no}", probe.note)
    if own_failing:
        user += ("\n\nTests the Builder wrote itself fail: " + ", ".join(own_failing) + ". The client's tests pass. "
                 "Work out for each failing test whether the TEST is wrong (it expects something the step doesn't "
                 "ask for, or computes the expected value wrongly) or the CODE is wrong. Your FIX list must say which: "
                 "'<test file>:<line> - the test is wrong - <what it must expect instead>' or a normal code item. "
                 "The verdict can't be PASS while a test fails.")
    if last_fix:
        user += ("\n\nYour FIX list from the last round was:\n" + "\n".join(f"{i}. {f}" for i, f in enumerate(last_fix, 1)))
    if disputes:
        user += ("\n\nThe Builder disputes these items and changed no code for them:\n"
                 + "\n".join(f"- {d}" for d in disputes)
                 + "\nRead those lines again. If the Builder is right, mark the requirement DONE. If not, keep it "
                   "MISSING or WRONG and say exactly which input gives the wrong result.")
    v = Verdict("")
    folder = job.folder / "checker"
    folder.mkdir(exist_ok=True)
    for attempt in (1, 2):
        # the second try answers without thinking: an empty first answer means it thought past its budget
        a = llm.ask(s, "checker", PROMPT, user if attempt == 1 else
                    user + "\n\nAnswer now, in exactly the asked form: one line per requirement, then VERDICT.",
                    max_tokens=24000 if attempt == 1 else 6000, temperature=0.1, think=attempt == 1)
        v = parse(a.text, len(requirements))
        v.by = llm.model_for(s, "checker")
        name = f"{len(list(folder.glob('*.md'))) + 1:02d}-round-{round_no}" + ("" if attempt == 1 else "-retry")
        (folder / f"{name}.md").write_text(v.raw or "(empty answer)", encoding="utf-8")
        if v.verdict:
            break
    if not v.verdict:
        v.verdict, v.question = "ASK", "The Checker's answer couldn't be read twice; look at the diff yourself."
    if probe and probe.usable and probe.failed:
        said = {int(m.group(1)): (m.group(2).upper().replace(" ", "-"), m.group(3).strip())
                for m in PROBE_LINE.finditer(re.sub(r"<think>.*?</think>", "", v.raw, flags=re.S))}
        for n, (kind, why) in said.items():
            if kind == "PROBE-WRONG" and n in probe.failed:
                probe_state["wrong"].add(n)   # accepted: not held against the code in later rounds either
        # a failing probe the Checker didn't explain counts against the code: the safe side
        code_wrong = [n for n in probe.failed if said.get(n, ("CODE-WRONG", ""))[0] != "PROBE-WRONG"]
        if code_wrong:
            if v.verdict != "ASK":
                if v.verdict == "PASS":
                    v.reasons.append("the Checker's own probes fail for " + ", ".join(f"R{n}" for n in code_wrong))
                v.verdict = "FIX"
            have = " ".join(v.fix_list)
            for n in code_wrong:
                if f"R{n}" not in have:
                    req = requirements[n - 1] if 0 < n <= len(requirements) else ""
                    v.fix_list.append(f"R{n} ({req}): the Checker's probe for it fails"
                                      + (f" - {said[n][1]}" if n in said and said[n][1] else ""))
            v.probe_code, v.probe_details = probe.code, probe.details
    if own_failing and v.verdict == "PASS":   # never a PASS with a failing test, whoever wrote it
        v.verdict = "FIX"
        v.fix_list = v.fix_list or [f"{f} fails: decide whether the test or the code is wrong against the "
                                    "requirements, and correct that one" for f in own_failing]
        v.reasons.append("tests the Builder wrote still fail")
    v.checks = report
    job.log(f"Checker · round {round_no} · {v.verdict}",
            "\n".join(v.reasons + [f"{i}. {f}" for i, f in enumerate(v.fix_list, 1)]
                      + ([f"ASK: {v.question}"] if v.verdict == "ASK" else [])) or "all requirements done")
    return v
