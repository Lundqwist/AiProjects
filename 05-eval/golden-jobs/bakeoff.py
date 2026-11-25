r"""Golden-job bake-off: which local model makes a client task pass its hidden checks?

Each job folder holds:
  task.md   what the client asked for (the only instructions the model gets)
  start\    the client's code as it was
  hidden\   checks copied in only when the work is checked, and removed again before the next try
  job.json  {"check": [command...], "timeout": seconds}

The model works through the Code tab's own pipeline (wb_code.change: read files, write whole files),
only on this PC (online AI off). If the check fails and tries are left, the model gets the check's
output, as a client's CI would show it, and tries again.

    .venv\Scripts\python.exe 05-eval\golden-jobs\bakeoff.py --models qwen2.5-coder:7b qwen2.5-coder:14b
    .venv\Scripts\python.exe 05-eval\golden-jobs\bakeoff.py --models qwen3-coder:30b --pipeline builder

With --pipeline builder, a "try" is a round: the Builder's own up to 3 build/test tries, then the hidden checks.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "app"))

import wb_code  # noqa: E402
from wb_settings import Settings  # noqa: E402

RUNS = HERE / ".runs"
TAIL = 60  # lines of check output given back to the model


def check(job: dict, folder: Path, hidden: Path) -> tuple[bool, str, float]:
    """Copy the hidden checks in, run the job's check, take them out again."""
    copied, kept = [], {}
    for f in hidden.rglob("*"):
        if f.is_file():
            target = folder / f.relative_to(hidden)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():  # the model made a file of the same name: keep it, put it back afterwards
                kept[target] = target.read_bytes()
            shutil.copy2(f, target)
            copied.append(target)
    started = time.monotonic()
    try:
        env = {**os.environ, "Logging__LogLevel__Default": "Warning", "Logging__LogLevel__Microsoft": "Warning"}
        r = subprocess.run(job["check"], cwd=folder, capture_output=True, text=True, encoding="utf-8", env=env,
                           errors="replace", timeout=job.get("timeout", 300), shell=False,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ok, out = r.returncode == 0, (r.stdout + "\n" + r.stderr).strip()
    except subprocess.TimeoutExpired:
        ok, out = False, f"The check did not finish within {job.get('timeout', 300)} seconds."
    finally:
        for t in copied:
            t.unlink(missing_ok=True)
        for t, data in kept.items():
            t.write_bytes(data)
    return ok, out, time.monotonic() - started


def tail(text: str, n: int = TAIL) -> str:
    return "\n".join(text.splitlines()[-n:])


def run_job(s: Settings, job_dir: Path, work: Path, tries: int) -> dict:
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    task = (job_dir / "task.md").read_text(encoding="utf-8")
    shutil.copytree(job_dir / "start", work)
    result = {"job": job["name"], "passed": False, "passed_on_try": None, "tries": [], "model_seconds": 0.0}
    request = task
    for n in range(1, tries + 1):
        started = time.monotonic()
        try:
            _, changes, answer = wb_code.change(s, work, request, big=False)
            error = None
        except Exception as e:  # noqa: BLE001 - a crash is a failed try, not a failed bake-off
            changes, answer, error = [], None, f"{type(e).__name__}: {e}"
        seconds = time.monotonic() - started
        written = wb_code.apply(work, changes) if changes else []
        ok, out, check_seconds = check(job, work, job_dir / "hidden")
        result["model_seconds"] += seconds
        result["tries"].append({"try": n, "seconds": round(seconds), "check_seconds": round(check_seconds),
                                "files": written, "notes": (answer.notes if answer else []),
                                "error": error, "passed": ok, "check_tail": tail(out, 25)})
        # beside the work folder, not in it: the model reads every text file in the folder on its next try
        (work.parent / f"{work.name}.try{n}-reply.md").write_text(answer.text if answer else (error or ""),
                                                                  encoding="utf-8")
        (work.parent / f"{work.name}.try{n}-check.txt").write_text(out, encoding="utf-8")
        if ok:
            result.update(passed=True, passed_on_try=n)
            break
        request = (f"{task}\n\nYour previous change was checked and the check failed. "
                   f"The last lines of its output:\n\n```\n{tail(out)}\n```\n\n"
                   "Fix the code so the check passes. The files above are the current state.")
    result["model_seconds"] = round(result["model_seconds"])
    return result


def allow_restore(title: str, text: str, buttons: list[str]) -> str:
    """The bake-off's own answer to the on-screen question: our golden jobs' package restores (their
    package lists are ours, in this repository) are allowed; anything else is not."""
    return buttons[0] if "restore" in text else buttons[-1]


def run_job_builder(s: Settings, job_dir: Path, work: Path, tries: int) -> dict:
    """The coding team's Builder (team\\builder.py) instead of the Code tab: edits, then the Runner builds
    and tests, up to 3 tries per round. After each round the hidden checks run, and their output is the
    next round's fix list, as a client's CI (and from Day 4 the Checker) would give it."""
    from team import builder, jobs
    job_def = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    task = (job_dir / "task.md").read_text(encoding="utf-8")
    job = jobs.create(work, "golden", job_def["name"], str(job_dir / "start"))
    job.log("restores allowed by the bake-off (the golden job's own package lists)")
    result = {"job": job_def["name"], "passed": False, "passed_on_try": None, "tries": [], "model_seconds": 0.0,
              "job_folder": str(job.folder)}
    fix = ""
    for n in range(1, tries + 1):
        r = builder.build_step(job, task, s, ask_fn=allow_restore, fix_list=fix,
                               on_event=lambda t, d: print(f"      {t}: {d}", flush=True))
        ok, out, check_seconds = check(job_def, job.repo, job_dir / "hidden")
        seconds = sum(t.seconds for t in r.tries)
        result["model_seconds"] += seconds
        result["tries"].append({"try": n, "builder": r.status, "builder_tries": len(r.tries), "seconds": seconds,
                                "applied": [a for t in r.tries for a in t.applied],
                                "refused": [x for t in r.tries for x in t.refused], "commit": r.commit,
                                "check_seconds": round(check_seconds), "passed": ok, "check_tail": tail(out, 25)})
        (job.folder / f"hidden-check-{n}.txt").write_text(out, encoding="utf-8")
        print(f"    round {n}: Builder {r.status} after {len(r.tries)} tries · hidden checks "
              f"{'PASS' if ok else 'FAIL'}", flush=True)
        if ok:
            result.update(passed=True, passed_on_try=n)
            break
        if r.status == "ask":
            break
        fix = f"The client's CI ran its own tests on your work and they failed. The last lines:\n\n{tail(out)}"
    result["model_seconds"] = round(result["model_seconds"])
    return result


def unload(model: str) -> None:
    subprocess.run(["ollama", "stop", model], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--jobs", nargs="*", help="job folder names (default: all)")
    ap.add_argument("--tries", type=int, default=2)
    ap.add_argument("--repeat", type=int, default=1, help="runs per job and model (models vary between runs)")
    ap.add_argument("--pipeline", choices=("code", "builder"), default="code",
                    help="code = the Code tab (whole files); builder = the coding team's Builder (edits + Runner)")
    args = ap.parse_args()
    run_one = run_job_builder if args.pipeline == "builder" else run_job

    jobs = sorted(p for p in HERE.iterdir() if (p / "job.json").exists() and (not args.jobs or p.name in args.jobs))
    run_dir = RUNS / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    results = []
    for model in args.models:
        s = Settings()
        s.code_model, s.online_mode = model, "off"
        for rep in range(1, args.repeat + 1):
            for job_dir in jobs:
                print(f"[{datetime.now():%H:%M:%S}] {model} · {job_dir.name} · run {rep} …", flush=True)
                work = run_dir / model.replace(":", "_") / f"{job_dir.name}-run{rep}"
                r = run_one(s, job_dir, work, args.tries)
                r.update(model=model, run=rep, pipeline=args.pipeline)
                results.append(r)
                state = f"PASS on try {r['passed_on_try']}" if r["passed"] else "FAIL"
                print(f"    {state} · model {r['model_seconds']} s", flush=True)
                (run_dir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        unload(model)

    # per job: "passed runs/runs (first-try passes)"
    lines = ["| Model | " + " | ".join(j.name for j in jobs) + " | Passed | First try | Model time |",
             "|---|" + "---|" * len(jobs) + "---|---|---|"]
    for model in args.models:
        mine = [r for r in results if r["model"] == model]
        cells = []
        for j in jobs:
            rs = [r for r in mine if r["job"] == j.name]
            cells.append(f"{sum(r['passed'] for r in rs)}/{len(rs)}")
        total = sum(r["model_seconds"] for r in mine)
        first = sum(r["passed_on_try"] == 1 for r in mine)
        lines.append(f"| {model} | " + " | ".join(cells) + f" | {sum(r['passed'] for r in mine)}/{len(mine)}"
                     f" | {first}/{len(mine)} | {total // 60} min {total % 60} s |")
    table = "\n".join(lines)
    (run_dir / "results.md").write_text(table + "\n", encoding="utf-8")
    print("\n" + table + f"\n\nDetails: {run_dir}")


if __name__ == "__main__":
    main()
