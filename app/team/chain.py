"""A project in phases: one coding-team job per phase, each starting from the last one's result.

    python app\\team\\cli.py chain E:\\DropShipV1 --client "DropShip" --phases E:\\DropShipV1\\docs\\phases

For each phase file (in name order): a job is made from the previous phase's job repository (the first from the
project folder itself), the Lead writes its card from the project's brief plus the phase request, and the team runs
it (loop.run_job). The chain stops at the first phase that isn't done: a later phase would build on a step that
didn't pass. Every finished phase is fetched back into the project folder as a branch `ai/phase-<n>`, and at the end
the project folder is switched to the last finished one, so its files are there to review. The project's own `main`
is never changed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from team import jobs, lead, loop, runner
from wb_settings import Settings


@dataclass
class PhaseResult:
    phase: str
    job: str
    status: str
    steps: list[tuple[str, str]] = field(default_factory=list)   # (status, step text)
    review: str = ""
    minutes: float = 0.0


def phase_files(folder: Path) -> list[Path]:
    return sorted(p for p in Path(folder).glob("*.md"))


def deliver(project: Path, job: jobs.Job, branch: str) -> str:
    """Fetch the job's branch into the project folder as `branch` (local git, nothing leaves the PC). An ai/phase-<n>
    branch from an earlier run of the chain is replaced: those branches are the chain's own output."""
    jobs.git(project, "fetch", "--quiet", str(job.repo), f"+{job.branch}:{branch}")
    return branch


def run_chain(s: Settings, project: Path, phases: list[Path], *, client: str, ask_fn: runner.AskFn,
              on_event: Callable[[str, str], None] | None = None, jobs_root: Path | None = None,
              brief_file: str = "BRIEF.md", start_at: int = 1, max_tries: int = 5,
              packages: list[str] | None = None, attempts: int = 3) -> list[PhaseResult]:
    """max_tries: Builder tries per step; 5 here, not 3: a project built from nothing needs more rounds."""
    project = Path(project).resolve()
    say = on_event or (lambda t, d: None)
    brief = (project / brief_file).read_text(encoding="utf-8") if (project / brief_file).exists() else ""
    root = Path(jobs_root or s.jobs_dir)
    source: str = str(project)
    # the requests are read before the switch: a phase branch holds the phase files as they were when it was made,
    # and a request clarified since must not be swapped back for the old one
    requests = [f.read_text(encoding="utf-8").strip() for f in phases]
    # start from main, or from the phase an earlier run delivered; never stand on a branch the chain will replace
    jobs.git(project, "switch", "--quiet", "main" if start_at == 1 else f"ai/phase-{start_at - 1}")
    results: list[PhaseResult] = []
    for n, f in enumerate(phases, 1):
        if n < start_at:
            continue
        request = requests[n - 1]
        title = request.splitlines()[0].lstrip("# ").strip()
        say(f"Phase {n} of {len(phases)}", title)
        started = datetime.now()
        job = jobs.create(root, client, title, source)
        job.log("chain", f"phase {n} of {len(phases)}: {f.name}\nfrom: {source}")
        if packages:   # the packages the user approved for the whole project
            (job.folder / "approved-packages.txt").write_text("".join(f"{p}\n" for p in packages), encoding="utf-8")
        lead.plan(s, job, (f"The project's brief:\n\n{brief}\n\n" if brief else "")
                  + f"This job is phase {n} of {len(phases)}. Do only this phase; earlier phases are already in the "
                    f"repository.\n\n{request}", cover=request)
        out = loop.run_job(s, job, ask_fn=ask_fn, on_event=on_event, max_tries=max_tries, attempts=attempts)
        r = PhaseResult(f.name, str(job.folder), out.status, [(st.status, st.step.splitlines()[0][:100])
                                                               for st in out.steps], out.review,
                        round((datetime.now() - started).total_seconds() / 60, 1))
        results.append(r)
        say(f"Phase {n}: {out.status}", f"{r.minutes} min · " + ", ".join(st for st, _ in r.steps))
        if out.status != "done":
            break
        deliver(project, job, f"ai/phase-{n}")
        source = str(job.repo)
    done = [i for i, r in enumerate(results, start_at) if r.status == "done"]
    if done:
        jobs.git(project, "switch", "--quiet", f"ai/phase-{done[-1]}")
        say("Delivered", f"{project} is on branch ai/phase-{done[-1]} (main is unchanged)")
    return results
