"""The coding team's loop for a job: Scout → Builder ⇄ Runner → Checker, step by step, with two gates.

    Start gate   nothing runs until you click Start (on screen; in the terminal a typed y)
    each step    Scout writes the requirements and places → Builder edits until build + tests pass (3 tries)
                 → commit → Checker runs the checks itself and reads the code against each requirement
                 → FIX goes back to the Builder (at most 2 FIX rounds) → PASS moves on, ASK stops and asks you
    Review gate  review.md in the job folder: branch, commits, checks, verdicts, open questions;
                 then it offers to open the job in VS Code. Nothing leaves the PC: you push and deliver.

Every model runs on this PC (team\\llm.py). Everything is written to job-log.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from team import builder, checker, edits, jobs, llm, runner, scout
from wb_settings import Settings

FIX_ROUNDS = 2
Event = Callable[[str, str], None]


@dataclass
class StepOutcome:
    step: str
    status: str                 # pass, fix (stopped with FIX left), ask, failed
    message: str = ""
    pack: scout.ContextPack | None = None
    verdicts: list[checker.Verdict] = field(default_factory=list)
    builds: list[builder.BuildResult] = field(default_factory=list)
    question: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "pass"


@dataclass
class JobOutcome:
    status: str                 # done, stopped, not started
    steps: list[StepOutcome] = field(default_factory=list)
    review: str = ""            # path of review.md


def _head(job: jobs.Job) -> str:
    return jobs.git(job.repo, "rev-parse", "HEAD")


def run_step(s: Settings, job: jobs.Job, step: str, *, ask_fn: runner.AskFn | None = None,
             on_event: Event | None = None, fix_rounds: int = FIX_ROUNDS, extra_fix: str = "",
             max_tries: int = builder.MAX_TRIES, attempts: int = 1) -> StepOutcome:
    """One step; `attempts` > 1 starts a failed step over (its unfinished work undone, the steps before it kept).
    The same step passed in one run and failed in the next (Day 7): a fresh attempt is often enough."""
    on_event = on_event or (lambda t, d: None)
    since = _head(job)
    out = None
    for attempt in range(1, attempts + 1):
        out = _run_step_once(s, job, step, ask_fn=ask_fn, on_event=on_event, fix_rounds=fix_rounds,
                             extra_fix=extra_fix, max_tries=max_tries)
        if out.ok or out.status == "ask" or attempt == attempts:
            break
        # back to where the step started: only inside the job's own repository (the team's copy)
        jobs.git(job.repo, "reset", "--quiet", "--hard", since)
        jobs.git(job.repo, "clean", "--quiet", "-fd")
        job.log(f"Step attempt {attempt} of {attempts} didn't pass: starting the step again", out.message[:1500])
        on_event(f"Attempt {attempt + 1} of {attempts}", "starting the step again from where it began")
    if out and attempts > 1:
        out.message = (f"(attempt {attempt} of {attempts}) " + out.message) if not out.ok else out.message
    return out


def _run_step_once(s: Settings, job: jobs.Job, step: str, *, ask_fn: runner.AskFn | None = None,
                   on_event: Event | None = None, fix_rounds: int = FIX_ROUNDS, extra_fix: str = "",
                   max_tries: int = builder.MAX_TRIES) -> StepOutcome:
    on_event = on_event or (lambda t, d: None)
    repo = job.repo.resolve()
    card = job.card()
    stack = (card.stack or ", ".join(runner.detect(repo)) or "web").split(",")[0].strip()
    files = builder.repo_files(repo)
    protected = edits.protected_tests(repo, job.client_files(), builder.test_project_dirs(repo))
    since = _head(job)

    on_event("Scout", f"reading the repository with {llm.model_for(s, 'scout')}")
    pack = scout.scout(s, repo, step, files, protected, title=card.title, goal=card.goal, stack=stack,
                       stack_notes=runner.pack_notes(stack), request=job.request())
    job.log("Scout · " + ("context pack" if pack.ok else "no usable pack (the Builder sees every file)"),
            pack.for_prompt() + (f"\n\nfiles: {', '.join(pack.files)}" if pack.files else "")
            + (f"\n\nproblems: {'; '.join(pack.problems)}" if pack.problems else ""))
    (job.folder / "scout").mkdir(exist_ok=True)
    (job.folder / "scout" / f"{len(list((job.folder / 'scout').glob('*.md'))) + 1:02d}.md").write_text(
        pack.raw, encoding="utf-8")
    out = StepOutcome(step, "failed", pack=pack)

    b = builder.build_step(job, step, s, ask_fn=ask_fn, on_event=on_event, pack=pack, fix_list=extra_fix,
                           max_tries=max_tries,
                           commit_message=f"Builder: {step.splitlines()[0][:72]}")
    out.builds.append(b)
    if not b.ok:
        out.status, out.message = b.status, b.message
        return out
    probe_state: dict = {}   # the Checker's probes: written once per step, run again every round
    for rnd in range(1, fix_rounds + 2):
        on_event("Checker", f"round {rnd}: running the checks and reading the code with "
                            f"{llm.model_for(s, 'checker')}")
        last = out.verdicts[-1] if out.verdicts else None
        v = checker.check(s, job, step, pack.requirements, since, ask_fn=ask_fn, round_no=rnd,
                          last_fix=last.fix_list if last else None, disputes=out.builds[-1].disputes or None,
                          probe_state=probe_state, already_done=out.builds[-1].already_done,
                          context_files=pack.files)
        out.verdicts.append(v)
        if v.verdict == "PASS":
            out.status, out.message = "pass", f"PASS after {rnd} Checker round(s)."
            return out
        if v.verdict == "ASK":
            out.status, out.question = "ask", v.question
            out.message = f"The Checker asks: {v.question}"
            return out
        if rnd > fix_rounds:
            out.status = "fix"
            out.message = (f"Stopped after {fix_rounds} FIX rounds. What the Checker still finds:\n" + v.fix_text())
            return out
        on_event("Builder", f"FIX round {rnd}: {len(v.fix_list)} item(s)")
        b = builder.build_step(job, step, s, ask_fn=ask_fn, on_event=on_event, pack=pack, fix_list=v.fix_text(),
                               max_tries=max_tries,
                               allow_disputes=rnd == 1,
                               commit_message=f"Builder: fix round {rnd} for {step.splitlines()[0][:50]}")
        out.builds.append(b)
        if not b.ok:
            out.status, out.message = b.status, b.message
            return out
    return out


def _summary(card: jobs.JobCard) -> str:
    steps = "<br>".join(f"{i}. {st.text}" for i, st in enumerate(card.steps, 1))
    return (f"<b>{card.title}</b> for {card.client}<br>{card.goal}<br><br>Steps:<br>{steps}<br><br>"
            "The team works only inside the job folder, on its own branch. Nothing is pushed or sent.")


def write_review(job: jobs.Job, outcome: JobOutcome) -> str:
    repo = job.repo
    base = job.base_commit()
    log = jobs.git(repo, "log", "--oneline", f"{base}..HEAD", check=False)
    stat = jobs.git(repo, "diff", "--stat", f"{base}..HEAD", check=False)
    card = job.card()
    lines = [f"# Review: {card.title}", "", f"Client: {card.client}", f"Branch: `{job.branch}` in `{repo}`",
             f"Result: **{outcome.status}**", "", "## Steps", ""]
    for i, st in enumerate(outcome.steps, 1):
        lines.append(f"{i}. **{st.status.upper()}** {st.step.splitlines()[0]}")
        if st.pack and st.pack.requirements:
            last = st.verdicts[-1] if st.verdicts else None
            for n, r in enumerate(st.pack.requirements, 1):
                state = last.requirements.get(n, ("?", ""))[0] if last and last.requirements else "?"
                lines.append(f"   - R{n} {state}: {r}")
        if st.message and not st.ok:
            lines += ["", "   " + st.message.replace("\n", "\n   ")]
    lines += ["", "## Commits", "", "```", log or "(none)", "```", "", "## Changed files", "", "```",
              stat or "(none)", "```", "", "## Checks (the Checker's own last run)", ""]
    last_checks = next((v.checks for st in reversed(outcome.steps) for v in reversed(st.verdicts) if v.checks), "")
    lines += ["```", last_checks or "(not run)", "```", "",
              "## Before it goes to the client", "",
              "- Read the diff (VS Code: Source Control, or `git diff " + base[:8] + "..HEAD`).",
              "- Push, deploy and the client note are yours; the team never does them."]
    questions = [st.question for st in outcome.steps if st.question]
    if questions:
        lines += ["", "## Questions for you", ""] + [f"- {q}" for q in questions]
    path = job.folder / "review.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def run_job(s: Settings, job: jobs.Job, *, ask_fn: runner.AskFn | None = None, on_event: Event | None = None,
            open_review: Callable[[jobs.Job], None] | None = None, max_tries: int = builder.MAX_TRIES,
            attempts: int = 1) -> JobOutcome:
    """Run every step of the job card that isn't passed yet, between the Start and Review gates."""
    on_event = on_event or (lambda t, d: None)
    card = job.card()
    todo = [st for st in card.steps if st.status != "pass"]
    if not todo:
        return JobOutcome("done", review=write_review(job, JobOutcome("done")))
    answer = ask_fn("Start this job?", _summary(card), ["Start", "Not now"]) if ask_fn else "Not now"
    if answer != "Start":
        job.log("Start gate · not started")
        return JobOutcome("not started")
    job.log("Start gate · started", "\n".join(f"{i}. {st.text}" for i, st in enumerate(card.steps, 1)))
    outcome = JobOutcome("done")
    for st in todo:
        st.status = "building"
        job.save_card(card)
        text = st.text + (f"\nCheck: {st.check}" if st.check else "")
        r = run_step(s, job, text, ask_fn=ask_fn, on_event=on_event, max_tries=max_tries, attempts=attempts)
        outcome.steps.append(r)
        st.status = {"pass": "pass", "ask": "ask", "fix": "fix"}.get(r.status, "failed")
        if r.question:
            card.questions.append(r.question)
        job.save_card(card)
        if not r.ok:
            outcome.status = "stopped"
            break
    outcome.review = write_review(job, outcome)
    job.log(f"Review gate · {outcome.status}", f"review: {outcome.review}")
    if open_review and ask_fn and ask_fn("Review the job?", f"The job is <b>{outcome.status}</b>. Open it in "
                                         "VS Code to read the diff?", ["Open in VS Code", "Not now"]) == "Open in VS Code":
        open_review(job)
    return outcome
