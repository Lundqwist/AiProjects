"""The coding team in the conversation: client jobs from your words, run in the background, reviewed by you.

    new_coding_job     a job folder with a copy of the client's code (asks on screen first) and a job card the Lead
                       writes from your request: goal, checks, steps, questions. Nothing runs yet.
    update_coding_job  the Lead changes the card from what you say (answers to its questions, changes of plan)
    start_coding_job   the team works through the card in the background: Start gate on screen, then Scout,
                       Builder and Checker per step; when it ends the conversation is told, with review.md
    coding_job_status  how a job is going; list_coding_jobs: the recent ones

One job runs at a time (one graphics card). The team works only inside the job folder, on its own branch:
nothing is pushed, deployed or sent. Everything outside the job folder asks on screen first (wb_access.ask).
"""
from __future__ import annotations

import threading
import traceback
from datetime import datetime
from pathlib import Path

import wb_access
from team import jobs, lead, loop
from wb_settings import Settings

_settings: Settings | None = None
_running: dict = {"job": None, "thread": None, "step": ""}
_lock = threading.Lock()


def configure(s: Settings) -> None:
    global _settings
    _settings = s


def _s() -> Settings:
    return _settings or Settings.load()


def _root() -> Path:
    return Path(_s().jobs_dir)


def _all_jobs() -> list[jobs.Job]:
    root = _root()
    if not root.is_dir():
        return []
    found = [jobs.Job(p.parent) for p in root.glob("*/*/job.md")]
    return sorted(found, key=lambda j: j.card_path.stat().st_mtime, reverse=True)


def _find(name: str) -> jobs.Job | None:
    """A job by its folder name, client, title or any words of them; the newest one when name is empty."""
    all_jobs = _all_jobs()
    if not name.strip():
        return all_jobs[0] if all_jobs else None
    want = [w for w in jobs.slug(name, 200).split("-") if w]
    for j in all_jobs:
        hay = f"{j.folder.parent.name}-{j.folder.name}-{jobs.slug(j.card().title, 200)}"
        if all(w in hay for w in want):
            return j
    return None


def _summary(job: jobs.Job) -> str:
    card = job.card()
    lines = [f"Job folder: {job.folder}", f"Branch: {job.branch}", "", card.to_markdown().strip()]
    return "\n".join(lines)


def new_coding_job(client: str, request: str, source: str) -> str:
    """Start a client coding job: copy the client's code into a job folder of its own (on a new git branch) and have
    the Lead write a job card (goal, checks, steps, questions) from the request. Nothing is built yet: show the user
    the card and its questions, then wait for them to say start (start_coding_job).

    Args:
        client: who the job is for (a client's name, or "own")
        request: what the client wants, with every detail the user gave
        source: where the code is: a folder on this PC (e.g. E:\\ProjectsCoding\\Website2.0) or a git URL
    """
    s = _s()
    where = source.strip().strip('"')
    if not where:
        return "Where is the client's code? Give a folder on this PC or a git URL."
    answer = wb_access.ask("Copy code for a new job?",
                           f"The coding team wants to copy <b>{where}</b> into a new job folder for <b>{client}</b>."
                           "<br><br>It works only on that copy, on a branch of its own; the original isn't changed.",
                           ["Copy it", "Don't copy"])
    if answer != "Copy it":
        return "Not copied: the user didn't allow it on screen, so no job was made."
    title = " ".join(request.split()[:8]) or "job"
    try:
        job = jobs.create(Path(s.jobs_dir), client or "own", title, where)
    except (FileNotFoundError, RuntimeError) as e:
        return f"No job was made: {e}"
    card, notes = lead.plan(s, job, request)
    out = _summary(job)
    if notes:
        out += "\n\nThe program changed: " + "; ".join(notes)
    out += ("\n\nIn your answer, show the user the job card: its title, its numbered steps and its questions (or "
            "that it has none). Then say they can ask you to change it, or to start the job (it asks on screen "
            "before it starts). Don't start it yourself.")
    return out


def update_coding_job(job: str, change: str) -> str:
    """Change a coding job's card from what the user says: answers to the Lead's questions, or changes of plan.

    Args:
        job: the job (its folder name, client or title; empty = the newest)
        change: what the user said, in full
    """
    j = _find(job)
    if not j:
        return f"No coding job matches '{job}'."
    if _running["job"] and Path(_running["job"]) == j.folder:
        return "That job is running now; change it when it has stopped."
    card, notes = lead.revise(_s(), j, change)
    return _summary(j) + (("\n\n" + "; ".join(notes)) if notes else "")


def _run(job: jobs.Job) -> None:
    import wb_notify
    s = _s()
    try:
        try:
            import wb_create
            wb_create.make_room()   # the team needs the whole graphics card
        except Exception:  # noqa: BLE001 - freeing memory is a help, not a need
            pass

        def event(title: str, detail: str) -> None:
            _running["step"] = f"{title}: {detail}"

        def open_review(j: jobs.Job) -> None:
            import wb_code
            wb_code.open_in_vscode(j.repo)

        out = loop.run_job(s, job, ask_fn=wb_access.ask, on_event=event, open_review=open_review)
        if out.status == "not started":
            wb_notify.announce(f"The coding job “{job.card().title}” wasn't started: Start wasn't clicked on screen.")
            return
        lines = [f"The coding job “{job.card().title}” is {'done' if out.status == 'done' else 'stopped'}."]
        for st in out.steps:
            lines.append(f"- {st.status.upper()}: {st.step.splitlines()[0][:90]}")
            if not st.ok and st.message:
                lines.append("  " + st.message.splitlines()[0][:200])
        lines.append(f"Branch {job.branch} in {job.repo}. Review it before anything goes to the client: {out.review}")
        wb_notify.announce("\n".join(lines), out.review)
    except Exception as e:  # noqa: BLE001 - nobody waits for this thread: tell the conversation and the log
        print(f"[{datetime.now():%H:%M:%S}] coding job failed:", flush=True)
        traceback.print_exc()
        wb_notify.announce(f"The coding job “{job.folder.name}” failed: {type(e).__name__}: {e}. Details in the log.")
    finally:
        with _lock:
            _running.update(job=None, thread=None, step="")


def start_coding_job(job: str = "") -> str:
    """Run a coding job's card with the coding team, in the background: it asks on screen before it starts, then the
    Scout, Builder and Checker work through each step on the job's own branch. When it ends the conversation is told,
    with the review file. Only when the user asks to start it.

    Args:
        job: the job (its folder name, client or title; empty = the newest)
    """
    if _running["thread"] is not None:   # before reading any card: the running job writes its own
        return f"A coding job is running ({Path(_running['job']).name}); one at a time."
    j = _find(job)
    if not j:
        return f"No coding job matches '{job}'."
    card = j.card()
    if not card.steps:
        return "The job card has no steps yet: update_coding_job first."
    with _lock:
        if _running["thread"] is not None:
            return f"A coding job is running ({Path(_running['job']).name}); one at a time."
        t = threading.Thread(target=_run, args=(j,), name="coding-job", daemon=True)
        _running.update(job=str(j.folder), thread=t, step="waiting for Start on screen")
        t.start()
    q = f" It still has open questions: {'; '.join(card.questions)}." if card.questions else ""
    return (f"Asked on screen to start “{card.title}” ({len(card.steps)} steps).{q} Once Start is clicked it runs in "
            "the background (often 10-40 minutes per step, all on this PC); the result will be posted here.")


def coding_job_status(job: str = "") -> str:
    """How a coding job is going: its steps, what it's doing now, and the last things it did.

    Args:
        job: the job (its folder name, client or title; empty = the newest)
    """
    j = _find(job)
    if not j:
        return f"No coding job matches '{job}'."
    running = _running["job"] and Path(_running["job"]) == j.folder
    heads = [ln[3:] for ln in j.log_path.read_text(encoding="utf-8").splitlines() if ln.startswith("## ")][-8:]
    review = j.folder / "review.md"
    return (_summary(j) + f"\n\nRunning now: {'yes, ' + _running['step'] if running else 'no'}"
            + "\n\nLast in its log:\n" + "\n".join(f"- {h}" for h in heads)
            + (f"\n\nReview: {review}" if review.exists() else ""))


def list_coding_jobs() -> str:
    """The recent coding jobs, newest first, with how far each one is."""
    all_jobs = _all_jobs()[:10]
    if not all_jobs:
        return f"No coding jobs yet (they go in {_root()})."
    out = []
    for j in all_jobs:
        c = j.card()
        done = sum(st.status == "pass" for st in c.steps)
        out.append(f"- {j.folder.parent.name}/{j.folder.name}: {c.title} ({done} of {len(c.steps)} steps passed)")
    return "\n".join(out)


TOOLS = {f.__name__: f for f in (new_coding_job, update_coding_job, start_coding_job, coding_job_status,
                                 list_coding_jobs)}
STEP_NAMES = {"new_coding_job": "Copying the code and planning the job…", "update_coding_job": "Changing the job card…",
              "start_coding_job": "Starting the coding team…", "coding_job_status": "Looking at the job…",
              "list_coding_jobs": "Listing the coding jobs…"}
