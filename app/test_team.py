"""Tests for the coding team's Runner and jobs (team\\runner.py, team\\jobs.py). No models, no network.

Run: .venv\\Scripts\\python.exe app\\test_team.py
Needs node, git and dotnet on this PC (dotnet only for finding projects; nothing is built with it).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from team import jobs, runner  # noqa: E402

failures: list[str] = []
TMP = Path(tempfile.mkdtemp(prefix="wb-team-test-"))


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(f"{name}: {detail}")


def node_project(folder: Path, scripts: dict, deps: dict | None = None, lock: bool = False) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    pj = {"name": folder.name, "version": "1.0.0", "private": True, "scripts": scripts}
    if deps:
        pj["dependencies"] = deps
    (folder / "package.json").write_text(json.dumps(pj), encoding="utf-8")
    if lock:
        (folder / "package-lock.json").write_text("{}", encoding="utf-8")
    return folder


# ---- a repository with several projects ---------------------------------------------------------

repo = TMP / "repo"
node_project(repo / "web", {"build": "node -e \"console.log('built ok')\"",
                            "test": "node -e \"console.error('Error: expected 3 got 4'); process.exit(1)\"",
                            "lint": "node -e \"console.log(process.env.FAKE_TOKEN || 'no token'); "
                                    "console.log(process.env.MY_API_KEY || 'no key')\""})
node_project(repo / "app", {"build": "vite build", "test": "echo \"Error: no test specified\" && exit 1"},
             deps={"vite": "^8.0.0"}, lock=True)
node_project(repo / "web" / "node_modules" / "dep", {"build": "x"})  # inside node_modules: not a project
(repo / "api").mkdir()
(repo / "api" / "Api.sln").write_text("", encoding="utf-8")
(repo / "api" / "Api").mkdir()
(repo / "api" / "Api" / "Api.csproj").write_text("<Project Sdk=\"Microsoft.NET.Sdk.Web\"></Project>", encoding="utf-8")
(repo / "api" / "Api.Tests").mkdir()
(repo / "api" / "Api.Tests" / "Api.Tests.csproj").write_text(
    "<Project><ItemGroup><PackageReference Include=\"Microsoft.NET.Test.Sdk\" /></ItemGroup></Project>", encoding="utf-8")
(repo / "tools").mkdir()
(repo / "tools" / "Tool.csproj").write_text("<Project></Project>", encoding="utf-8")

projects = {p.rel(repo) for p in runner.find_projects(repo)}
check("finds projects, a .sln stands for the .csproj files under it, node_modules is skipped",
      projects == {"web/package.json", "app/package.json", "api/Api.sln", "tools/Tool.csproj"}, str(projects))
check("detects the web pack", runner.detect(repo) == ["web"], str(runner.detect(repo)))

build = {s.project.rel(repo): s for s in runner.plan(repo, "build")}
check("build is planned for every project with a build", set(build) == projects, str(set(build)))
check("dotnet build doesn't restore by itself", "--no-restore" in build["api/Api.sln"].command)
check("a .sln without restored packages needs a restore",
      bool(build["api/Api.sln"].missing) and "Api.csproj" in build["api/Api.sln"].missing, str(build["api/Api.sln"].missing))
check("a node project with dependencies but no node_modules needs a restore", build["app/package.json"].missing == "node_modules")
check("a node project without dependencies needs nothing", build["web/package.json"].missing is None)

tests = {s.project.rel(repo) for s in runner.plan(repo, "test")}
check("test only where there is a real test (npm's default and non-test projects left out)",
      tests == {"web/package.json", "api/Api.sln"}, str(tests))
restore = {s.project.rel(repo): s for s in runner.plan(repo, "restore")}
check("restore asks first", all(s.ask for s in restore.values()) and "app/package.json" in restore, str(restore))
check("npm restore only where there is a lockfile", "web/package.json" not in restore)
try:
    runner.plan(repo, "deploy")
    check("an unknown action is refused", False)
except ValueError:
    check("an unknown action is refused", True)

# ---- running ------------------------------------------------------------------------------------

r = runner.run(repo, "build", only="web/package.json")
check("a build that works is OK", len(r) == 1 and r[0].ok, runner.report(r, "build"))

r = runner.run(repo, "test", only="web/package.json")[0]
check("a failing test is reported with its error line",
      not r.ok and r.exit_code == 1 and any("expected 3 got 4" in e for e in r.errors), r.summary())

os.environ["FAKE_TOKEN"], os.environ["MY_API_KEY"] = "tok-123-secret", "key-456-secret"
r = runner.run(repo, "lint", only="web/package.json")[0]
check("secrets in the environment are not passed on", r.ok and "secret" not in r.output and "no token" in r.output,
      r.output)

r = runner.run(repo, "build", only="app/package.json")[0]
check("a missing restore stops the step before it runs", r.state == "needs restore" and not r.ok, r.summary())

asked: list[str] = []
r = runner.run(repo, "restore", only="app/package.json",
               ask_fn=lambda t, text, b: (asked.append(text), b[-1])[1])[0]
check("an ask-first action is asked and refused on no", r.state == "refused" and asked and "npm ci" in asked[0],
      r.summary())
r = runner.run(repo, "restore", only="app/package.json")[0]
check("an ask-first action with no one to ask is refused", r.state == "refused")

slow = node_project(repo / "slow", {"test": "node -e \"setTimeout(() => {}, 60000)\""})
step = runner.plan(repo, "test", only="slow/package.json")[0]
step.timeout = 3
started = time.monotonic()
r = runner.run_step(repo, step)
check("a step past its time limit is stopped", r.state == "timed out" and time.monotonic() - started < 20,
      f"{r.state}, {time.monotonic() - started:.0f} s")

step.cwd = TMP
check("a step outside the repository is refused", runner.run_step(repo, step).state == "refused")

check("paths in the repository are shortened",
      runner.shorten_paths(f"{repo}\\web\\a.js(3,1): error\nfile:///{repo.as_posix()}/b.js", repo) ==
      "web\\a.js(3,1): error\nb.js")

lines = ["building", "error during build:", "[transform] Unexpected token", "  ╭─[ src/App.jsx:3:5 ]",
         " 3 │ <div>", "done", "x", "y", "z", "unrelated"]
check("error lines keep the code frame after them",
      runner.error_lines(lines)[:4] == lines[1:5] and "unrelated" not in runner.error_lines(lines))

# ---- job card -----------------------------------------------------------------------------------

card = jobs.JobCard("Add booking page", "Acme", "web", "Customers can book a time",
                    ["dotnet test passes", "GET /book returns 200"], "payments",
                    [jobs.Step("Add the endpoint", "dotnet test passes", "pass"),
                     jobs.Step("Add the page", "npm run build passes", "fix"), jobs.Step("Link it from the menu")],
                    ["Which opening hours?"])
back = jobs.JobCard.from_markdown(card.to_markdown())
check("a job card survives Markdown", back == card, f"{back}")
empty = jobs.JobCard.from_markdown(jobs.JobCard("Only a title").to_markdown())
check("an empty job card survives Markdown", empty == jobs.JobCard("Only a title"), f"{empty}")

# ---- creating jobs ------------------------------------------------------------------------------

plain = TMP / "client-zip"
node_project(plain / "site", {"build": "node -e \"console.log(1)\""})
node_project(plain / "site" / "node_modules" / "big", {})
job = jobs.create(TMP / "Jobs", "Acme AB", "Fix the menu!", str(plain))
check("a job from a plain folder: copied without node_modules, own git repo, own branch",
      (job.repo / "site" / "package.json").exists() and not (job.repo / "site" / "node_modules").exists()
      and jobs.git(job.repo, "branch", "--show-current") == "ai/fix-the-menu", job.folder.name)
check("job folder name and card", job.folder.parent.name == "acme-ab" and job.card().stack == "web"
      and job.card().client == "Acme AB", job.card().to_markdown())
check("the original folder is untouched", not (plain / ".git").exists())

src = TMP / "client-git"
node_project(src, {"build": "node -e \"console.log(1)\""})
jobs.git(src, "init", "--quiet")
jobs.git(src, "add", "-A")
jobs.git(src, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", "first")
(src / "uncommitted.txt").write_text("not part of the job", encoding="utf-8")
job2 = jobs.create(TMP / "Jobs", "Acme AB", "Fix the menu!", str(src))
check("a second job with the same name gets its own folder", job2.folder != job.folder and job2.folder.name.endswith("-2"))
check("a job from a git folder: cloned, committed work only, own branch",
      (job2.repo / "package.json").exists() and not (job2.repo / "uncommitted.txt").exists()
      and jobs.git(job2.repo, "branch", "--show-current") == "ai/fix-the-menu-2")
job2.run("build")
log = job2.log_path.read_text(encoding="utf-8")
check("runs are written to the job log", "job created" in log and "build · package.json · OK" in log, log)
(job2.repo / "bin").mkdir()
(job2.repo / "bin" / "App.dll").write_bytes(b"x")
(job2.repo / "node_modules" / "dep").mkdir(parents=True)
check("build output is never part of the team's commits (local exclude)",
      jobs.git(job2.repo, "status", "--porcelain") == "", jobs.git(job2.repo, "status", "--porcelain"))
check("the job remembers the commit it started from", jobs.Job(job2.folder).base_commit()
      == jobs.git(job2.repo, "rev-parse", "HEAD"))
check("Job.open finds the job from inside its repo", jobs.Job.open(job2.repo / "package.json").folder == job2.folder)
try:
    jobs.create(TMP / "Jobs", "x", "y", str(TMP / "does-not-exist"))
    check("a missing source is refused and leaves no folder", False)
except FileNotFoundError:
    check("a missing source is refused and leaves no folder", not (TMP / "Jobs" / "x").exists()
          or not any((TMP / "Jobs" / "x").iterdir()))

shutil.rmtree(TMP, ignore_errors=True)
if failures:
    print("\n" + "\n".join(failures))
    sys.exit(1)
print("\nAll team tests passed.")
