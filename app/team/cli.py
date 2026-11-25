r"""Command line for the coding team's jobs and Runner (until the Lead runs them from the chat, Day 7).

    .venv\Scripts\python.exe app\team\cli.py new --client "Acme" --title "Add booking page" --source E:\Code\acme-site
    .venv\Scripts\python.exe app\team\cli.py projects <job>
    .venv\Scripts\python.exe app\team\cli.py plan <job> build
    .venv\Scripts\python.exe app\team\cli.py run <job> build
    .venv\Scripts\python.exe app\team\cli.py run <job> restore          (asks in this window first)
    .venv\Scripts\python.exe app\team\cli.py card <job>
    .venv\Scripts\python.exe app\team\cli.py build <job> "Add GET /api/items/{id} with 404"   (the Builder)

<job> is the job folder, its repo folder or anything inside it.
Actions: restore, build, test, lint, typecheck. Ask-first actions ask here, typed y; with no one at the
keyboard they are refused.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # the app folder

from team import jobs, runner  # noqa: E402
from wb_settings import Settings  # noqa: E402


def ask_here(title: str, text: str, buttons: list[str]) -> str:
    """The terminal's version of the on-screen question: only a typed y at a real keyboard says yes."""
    import re
    plain = re.sub(r"<br\s*/?>", "\n", re.sub(r"</?b>", "", text))
    if not sys.stdin.isatty():
        print(f"\n{title}\n{plain}\n-> no one to ask here, so: {buttons[-1]}")
        return buttons[-1]
    try:  # Windows calls the null device a terminal too: reading it ends at once, which counts as no
        reply = input(f"\n{title}\n{plain}\n{buttons[0]}? [y/N] ").strip().lower()
    except EOFError:
        print(f"-> no answer, so: {buttons[-1]}")
        return buttons[-1]
    return buttons[0] if reply in ("y", "yes") else buttons[-1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new", help="make a job folder with the client's code on a branch of its own")
    n.add_argument("--client", required=True)
    n.add_argument("--title", required=True)
    n.add_argument("--source", required=True, help="a folder or a git URL")
    n.add_argument("--jobs-dir", help="default: the Jobs folder in AI Workbench's settings")
    for name, helptext in (("projects", "list the projects and stack packs found"), ("card", "show the job card")):
        sub.add_parser(name, help=helptext).add_argument("job")
    for name, helptext in (("plan", "show what an action would run"), ("run", "run an action")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("job")
        p.add_argument("action", choices=runner.ACTIONS)
        p.add_argument("--only", help="one project, by its path in the repo (e.g. site/Lundqwist.Site.csproj)")
    b = sub.add_parser("build", help="the Builder makes one step true (code model, up to 3 tries), then commits")
    b.add_argument("job")
    b.add_argument("step", help="what to do, in words (or @file.md to read it from a file)")
    b.add_argument("--tries", type=int, default=3)
    b.add_argument("--no-commit", action="store_true")
    a = ap.parse_args()

    if a.cmd == "new":
        root = Path(a.jobs_dir or Settings.load().jobs_dir)
        job = jobs.create(root, a.client, a.title, a.source)
        print(f"Job folder: {job.folder}\nBranch:     {job.branch}\n")
        print(job.log_path.read_text(encoding="utf-8"))
        return 0

    job = jobs.Job.open(a.job)
    if a.cmd == "card":
        print(job.card_path.read_text(encoding="utf-8"))
    elif a.cmd == "projects":
        print("Stack packs:", ", ".join(runner.detect(job.repo)) or "none")
        for p in runner.find_projects(job.repo):
            print(f"  {p.pack:5} {p.kind:7} {p.rel(job.repo)}")
    elif a.cmd == "plan":
        steps = runner.plan(job.repo, a.action, a.only)
        if not steps:
            print(f"No project has a '{a.action}' action.")
        for s in steps:
            flags = (" [asks first]" if s.ask else "") + (f" [needs restore: {s.missing}]" if s.missing else "")
            print(f"{s.project.rel(job.repo)}: {' '.join(s.command)}  (limit {s.timeout} s){flags}")
    elif a.cmd == "build":
        from team import builder
        step = Path(a.step[1:]).read_text(encoding="utf-8") if a.step.startswith("@") else a.step
        r = builder.build_step(job, step, ask_fn=ask_here, max_tries=a.tries, commit=not a.no_commit,
                               on_event=lambda t, d: print(f"· {t}: {d}", flush=True))
        print(f"\n{r.status.upper()}: {r.message}")
        if r.commit:
            print(f"Commit {r.commit} on {job.branch}")
        return 0 if r.ok else 1
    elif a.cmd == "run":
        results = job.run(a.action, only=a.only, ask_fn=ask_here)
        print(runner.report(results, a.action))
        return 0 if results and all(r.ok for r in results) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
