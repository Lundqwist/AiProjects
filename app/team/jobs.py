"""Jobs for the coding team: one folder per client task.

    Jobs\\<client>\\<date>-<slug>\\
        job.md        the job card (what the client wants, the checks, the steps)
        job-log.md    everything the team did, newest last
        repo\\         the client's code: a git clone on branch ai/<slug>, or a copy

The original is never touched: a local repository is cloned (committed work only), a folder that
isn't a git repository is copied and given a git repository of its own, so every step can be a commit.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from team import runner

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GIT_URL = re.compile(r"^(https?://|git@|ssh://)")


def git_exe() -> str:
    """Git for Windows if it is installed; the first git on PATH otherwise."""
    for p in (Path(r"C:\Program Files\Git\cmd\git.exe"), Path(r"C:\Program Files (x86)\Git\cmd\git.exe")):
        if p.exists():
            return str(p)
    found = shutil.which("git")
    if not found:
        raise FileNotFoundError("Git is not installed")
    return found


def git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run([git_exe(), *args], cwd=repo, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", creationflags=NO_WINDOW)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()[:400]}")
    return r.stdout.strip()


def slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:n].rstrip("-") or "job")


# ---- the job card -------------------------------------------------------------------------------

@dataclass
class Step:
    text: str
    check: str = ""
    status: str = "todo"     # todo, building, checking, pass, fix, ask, failed

    def line(self, n: int) -> str:
        """One numbered line; a step of several lines continues on lines indented by 4 spaces."""
        mark = {"pass": "x", "todo": " "}.get(self.status, "~")
        check = f" - check: {self.check}" if self.check else ""
        tag = "" if self.status in ("todo", "pass") else f" ({self.status})"
        first, *rest = self.text.split("\n")
        return f"{n}. [{mark}] {first}{check}{tag}" + "".join(f"\n    {r}" if r.strip() else "\n    ." for r in rest)


@dataclass
class JobCard:
    title: str
    client: str = "own"
    stack: str = ""
    goal: str = ""
    checks: list[str] = field(default_factory=list)
    out_of_scope: str = ""
    steps: list[Step] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        out = [f"# Job: {self.title}", "", f"Client: {self.client}", f"Stack pack: {self.stack}",
               f"Goal: {self.goal}", "", "Acceptance checks:"]
        out += [f"- {c}" for c in self.checks] or ["- (none yet)"]
        out += ["", f"Out of scope: {self.out_of_scope}", "", "Steps:"]
        out += [s.line(i) for i, s in enumerate(self.steps, 1)] or ["(none yet)"]
        out += ["", "Questions for Bjarne:"]
        out += [f"{i}. {q}" for i, q in enumerate(self.questions, 1)] or ["none"]
        return "\n".join(out) + "\n"

    @classmethod
    def from_markdown(cls, text: str) -> "JobCard":
        card, section = cls(title=""), ""
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("# Job:"):
                card.title = line[6:].strip()
            elif line.startswith("Client:"):
                card.client = line[7:].strip()
            elif line.startswith("Stack pack:"):
                card.stack = line[11:].strip()
            elif line.startswith("Goal:"):
                card.goal = line[5:].strip()
            elif line.startswith("Out of scope:"):
                card.out_of_scope, section = line[13:].strip(), ""
            elif line.rstrip(":") in ("Acceptance checks", "Steps", "Questions for Bjarne"):
                section = line.rstrip(":")
            elif section == "Acceptance checks" and line.startswith("- ") and line != "- (none yet)":
                card.checks.append(line[2:].strip())
            elif section == "Steps" and raw.startswith("    ") and card.steps:
                card.steps[-1].text += "\n" + ("" if raw.strip() == "." else raw[4:])
            elif section == "Steps":
                m = re.match(r"\d+\.\s*(?:\[(.)\]\s*)?(.*?)(?:\s+-\s+check:\s*(.*?))?(?:\s+\((\w+)\))?$", line)
                if m:
                    mark, body, check, tag = m.groups()
                    status = tag or ("pass" if mark == "x" else "todo")
                    card.steps.append(Step(body.strip(), (check or "").strip(), status))
            elif section == "Questions for Bjarne":
                m = re.match(r"\d+\.\s*(.+)", line)
                if m:
                    card.questions.append(m.group(1).strip())
        return card


# ---- a job --------------------------------------------------------------------------------------

@dataclass
class Job:
    folder: Path

    @property
    def repo(self) -> Path:
        return self.folder / "repo"

    @property
    def card_path(self) -> Path:
        return self.folder / "job.md"

    @property
    def log_path(self) -> Path:
        return self.folder / "job-log.md"

    @property
    def branch(self) -> str:
        return f"ai/{self.folder.name.split('-', 3)[-1]}"

    def card(self) -> JobCard:
        return JobCard.from_markdown(self.card_path.read_text(encoding="utf-8"))

    def save_card(self, card: JobCard) -> None:
        """Written to a temporary file and swapped in, so a reader never sees half a card."""
        tmp = self.card_path.with_suffix(".md.tmp")
        tmp.write_text(card.to_markdown(), encoding="utf-8")
        for attempt in range(20):   # Windows refuses the swap while a reader has the card open: wait a moment
            try:
                tmp.replace(self.card_path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)

    def log(self, title: str, body: str = "") -> None:
        entry = f"\n## {datetime.now():%Y-%m-%d %H:%M:%S} · {title}\n"
        if body:
            entry += f"\n```\n{body.rstrip()}\n```\n"
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(entry)

    def log_result(self, r: runner.Result) -> None:
        state = "OK" if r.ok else r.state if r.state != "ran" else f"failed (exit {r.exit_code})"
        self.log(f"{r.action} · {r.project} · {state} · {r.seconds:.0f} s",
                 "" if r.ok else f"$ {r.command}\n" + "\n".join(r.output.splitlines()[-40:]))

    def request(self, limit: int = 6000) -> str:
        """The request the job was planned from (request.md), so the team can read the details the steps leave out."""
        f = self.folder / "request.md"
        if not f.exists():
            return ""
        text = f.read_text(encoding="utf-8").strip()
        return text if len(text) <= limit else text[:limit] + "\n…(cut here)"

    def base_commit(self) -> str:
        """The commit the job started from: the client's code before the team changed anything."""
        f = self.folder / "base-commit"
        if f.exists():
            return f.read_text(encoding="utf-8").strip()
        for line in git(self.repo, "log", "--format=%H %an").splitlines():  # older jobs: skip the team's commits
            sha, _, author = line.partition(" ")
            if not author.startswith("AI Workbench Builder"):
                return sha
        raise RuntimeError("the job's repository has no commit from before the team started")

    def client_files(self) -> list[str]:
        """The files in the client's code as it was received (relative, forward slashes)."""
        return git(self.repo, "ls-tree", "-r", "--name-only", self.base_commit()).splitlines()

    def run(self, action: str, *, only: str | None = None, ask_fn: runner.AskFn | None = None) -> list[runner.Result]:
        return runner.run(self.repo, action, only=only, ask_fn=ask_fn, log=self.log_result)

    @classmethod
    def open(cls, path: str | Path) -> "Job":
        """A job from its folder, its repo folder or anything inside it."""
        p = Path(path).resolve()
        for cand in [p, *p.parents]:
            if (cand / "job.md").exists():
                return cls(cand)
        raise FileNotFoundError(f"{path} is not inside a job folder (no job.md found)")


def _copy_ignore(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n in runner.SKIP_DIRS and n != ".git"}


def create(jobs_root: Path, client: str, title: str, source: str) -> Job:
    """A new job folder with the client's code in repo\\ on a branch of its own."""
    folder = Path(jobs_root) / slug(client, 30) / f"{datetime.now():%Y-%m-%d}-{slug(title)}"
    base, n = folder, 2
    while folder.exists():
        folder, n = base.with_name(f"{base.name}-{n}"), n + 1
    folder.mkdir(parents=True)
    job = Job(folder)
    src = Path(source) if not GIT_URL.match(source) else None
    try:
        if src is None:
            git(folder, "clone", "--quiet", source, "repo")
            how = f"cloned {source}"
        elif not src.is_dir():
            raise FileNotFoundError(f"{source} is not a folder or a git URL")
        elif (src / ".git").exists():
            git(folder, "clone", "--quiet", "--no-hardlinks", str(src.resolve()), "repo")
            how = f"cloned {src} (committed work only; uncommitted changes there are not included)"
        else:
            shutil.copytree(src, job.repo, ignore=_copy_ignore)
            git(job.repo, "init", "--quiet")
            git(job.repo, "add", "-A")
            git(job.repo, "-c", "user.name=AI Workbench", "-c", "user.email=workbench@localhost",
                "commit", "--quiet", "-m", "The client's code as received")
            how = f"copied {src} (not a git repository: a new one was made here)"
        # -C: a clone can bring a branch of the same name (the source was on it); the job's branch starts here
        git(job.repo, "switch", "--quiet", "-C", job.branch)
        # build output never goes into the team's commits, whatever the client's .gitignore says;
        # .git\info\exclude is local to this clone, so the client's files stay as they are
        exclude = job.repo / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as f:
            f.write("\n# AI Workbench: build output\n"
                    + "".join(f"{d}/\n" for d in sorted(runner.SKIP_DIRS - {".git"}))
                    + "*.user\nTestResults/\n.gradle/\n.kotlin/\nlocal.properties\n.buildResult/\n.sign/\n")
        (folder / "base-commit").write_text(git(job.repo, "rev-parse", "HEAD") + "\n", encoding="utf-8")
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    packs = runner.detect(job.repo)
    job.save_card(JobCard(title=title, client=client, stack=", ".join(packs) or "unknown"))
    job.log_path.write_text(f"# Log: {title}\n", encoding="utf-8")
    projects = runner.find_projects(job.repo)
    job.log("job created", f"{how}\nbranch {job.branch}\nstack packs: {', '.join(packs) or 'none found'}\n"
                           "projects:\n" + "\n".join(f"  {p.kind:7} {p.rel(job.repo)}" for p in projects))
    return job
