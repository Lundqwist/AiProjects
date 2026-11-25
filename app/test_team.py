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

# ---- the lua pack -------------------------------------------------------------------------------

addon = TMP / "lua-repo" / "MyAddon"
(addon / "tests").mkdir(parents=True)
(addon / "MyAddon.toc").write_text("## Interface: 16001\n## SavedVariables: MyAddonDB\n\nCore.lua\n", encoding="utf-8")
(addon / "Core.lua").write_text("local ADDON_NAME, ns = ...\nMyAddonDB = MyAddonDB or {}\nSLASH_MYADDON1 = '/my'\n",
                                encoding="utf-8")
lua_repo = addon.parent
check("the lua pack finds an addon by its .toc", [p.rel(lua_repo) for p in runner.find_projects(lua_repo)]
      == ["MyAddon/MyAddon.toc"] and runner.detect(lua_repo) == ["lua"])
r = runner.run(lua_repo, "build")
check("the lua build check passes a clean addon", len(r) == 1 and r[0].ok, runner.report(r, "build"))
(addon / "Core.lua").write_text("local ADDON_NAME, ns = ...\ncount = 1\nfor i = 1, 2 do goto skip ::skip:: end\n",
                                encoding="utf-8")
r = runner.run(lua_repo, "build")[0]
check("the lua build check reports a global and goto with their lines",
      not r.ok and any("Core.lua:2: sets the global 'count'" in e for e in r.errors)
      and any("Core.lua:3: goto" in e for e in r.errors), r.summary())
check("no test action without tests/run.lua", runner.plan(lua_repo, "test") == [])
(addon / "tests" / "run.lua").write_text("print('1 tests, 1 passed') os.exit(0)\n", encoding="utf-8")
r = runner.run(lua_repo, "test")
check("tests/run.lua is the test action", len(r) == 1 and r[0].ok, runner.report(r, "test"))

# ---- the tizen and android packs ----------------------------------------------------------------

tv = TMP / "tv-repo"
(tv / "other").mkdir(parents=True)
(tv / "other" / "config.xml").write_text("<widget><content src='x.html'/></widget>", encoding="utf-8")  # Cordova-like
(tv / "app" / "js").mkdir(parents=True)
(tv / "app" / "config.xml").write_text('<widget xmlns:tizen="http://tizen.org/ns/widgets"><content src="index.html"/>'
                                       '</widget>', encoding="utf-8")
(tv / "app" / "index.html").write_text('<script src="js/a.js"></script>', encoding="utf-8")
(tv / "app" / "js" / "a.js").write_text("var A = { name: cfg && cfg.name };\n", encoding="utf-8")
check("a config.xml is a Tizen app only with the Tizen namespace",
      [p.rel(tv) for p in runner.find_projects(tv)] == ["app/config.xml"] and runner.detect(tv) == ["tizen"])
r = runner.run(tv, "build")
check("the tizen build check passes ES2018 code", len(r) == 1 and r[0].ok, runner.report(r, "build"))
(tv / "app" / "js" / "a.js").write_text("var A = { name: cfg?.name ?? 'TV' };\n", encoding="utf-8")
r = runner.run(tv, "build")[0]
check("the tizen build check refuses ?. and ?? with the line", not r.ok and any("js/a.js:1:" in e and "Chrome 69" in e
                                                                              for e in r.errors), r.summary())
check("no tizen test action without *.test.js files", runner.plan(tv, "test") == [])
(tv / "app" / "tests").mkdir()
(tv / "app" / "tests" / "a.test.js").write_text("require('node:test')('x', () => {});\n", encoding="utf-8")
check("node --test is the tizen test action once there are tests",
      [s.command[:2] for s in runner.plan(tv, "test")] == [["node", "--test"]])

droid = TMP / "android-repo"
(droid / "app").mkdir(parents=True)
(droid / "settings.gradle.kts").write_text('include(":app")\n', encoding="utf-8")
(droid / "gradlew.bat").write_text("@echo off\n", encoding="utf-8")
check("an Android project is found by settings.gradle.kts with gradlew.bat",
      [p.rel(droid) for p in runner.find_projects(droid)] == ["settings.gradle.kts"] and runner.detect(droid) == ["android"])
b = runner.plan(droid, "build")[0]
check("android builds through the manifest check, with Android Studio's Java and the SDK from the environment",
      b.command[0] == "node" and b.command[1].endswith("android/build.js")
      and b.env.get("JAVA_HOME", "").endswith("Android Studio/jbr")
      and b.env.get("ANDROID_HOME", "").endswith("Android/Sdk") and "{env:" not in b.env["ANDROID_HOME"], str(b.env))
(droid / "app" / "src" / "test" / "java").mkdir(parents=True)
(droid / "app" / "src" / "test" / "java" / "ATest.java").write_text("class ATest {}\n", encoding="utf-8")
t = runner.plan(droid, "test")[0]
check("android tests run offline, with every failed test printed",
      "--offline" in t.command and any(c.endswith("android/test-logging.gradle") for c in t.command), str(t.command))
check("android restore asks first, and is the only action without --offline",
      all(s.ask and "--offline" not in s.command for s in runner.plan(droid, "restore")))

# ---- job card -----------------------------------------------------------------------------------

card = jobs.JobCard("Add booking page", "Acme", "web", "Customers can book a time",
                    ["dotnet test passes", "GET /book returns 200"], "payments",
                    [jobs.Step("Add the endpoint", "dotnet test passes", "pass"),
                     jobs.Step("Add the page", "npm run build passes", "fix"), jobs.Step("Link it from the menu")],
                    ["Which opening hours?"])
back = jobs.JobCard.from_markdown(card.to_markdown())
check("a job card survives Markdown", back == card, f"{back}")
multi = jobs.JobCard("Cart", steps=[jobs.Step("Two changes:\n\n1. **Bug:** rounding\n   - indented detail", "", "fix"),
                                     jobs.Step("Second")])
back = jobs.JobCard.from_markdown(multi.to_markdown())
check("a step of several lines (blank and indented lines too) survives Markdown", back == multi,
      f"{back.steps!r}")
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
