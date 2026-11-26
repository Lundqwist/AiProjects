"""The Builder: makes one step of a job true in the code, and leaves it building with its tests passing.

One try = the code model writes SEARCH/REPLACE edits (team\\edits.py), the program applies them, and the
Runner builds and tests every project in the job's repository. When something fails, the next try gets
the current files, which edits were refused and why, and the Runner's error lines, with the failing tests
named as the client's or the Builder's own. At most 3 tries per step; then it stops and reports.

Until the Scout exists (Day 4), the Builder is shown every source file of the repository (up to a limit).
A step that passes is committed on the job's branch, so every step can be looked at or undone on its own.
Restores are ask-first (on screen); without an OK the step stops and says so.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import wb_ai
import wb_code
from team import edits, jobs, runner
from wb_settings import Settings

PROMPT = (Path(__file__).resolve().parent / "prompts" / "builder.md").read_text(encoding="utf-8")
CONTEXT_CHARS = 60000
FILE_CHARS = 20000
SKIP_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "composer.lock", "packages.lock.json"}
MAX_TRIES = 3
MAX_EDITS = 30   # more blocks than this in one reply: the model is repeating itself

Event = Callable[[str, str], None]   # (title, detail): progress for a screen or a terminal


@dataclass
class TryRecord:
    n: int
    seconds: float
    applied: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    checks: str = ""            # the Runner's report
    ok: bool = False


@dataclass
class BuildResult:
    status: str                 # pass, failed, ask
    step: str
    tries: list[TryRecord] = field(default_factory=list)
    message: str = ""           # for the person (and, later, the Checker)
    commit: str = ""
    new_files: list[str] = field(default_factory=list)
    disputes: list[str] = field(default_factory=list)   # FIX items the Builder says are wrong
    own_failing: list[str] = field(default_factory=list)  # its own test files that still fail (the Checker decides)
    already_done: str = ""                                 # it said the code already does the step (and why)
    changed_files: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "pass"


# ---- what the Builder sees ----------------------------------------------------------------------

def repo_files(repo: Path) -> list[str]:
    out = []
    for p in wb_code.project_files(repo):
        rel = p.relative_to(repo).as_posix()
        if p.name in SKIP_FILES or edits.is_secret(rel):
            continue
        out.append(rel)
    return out


def test_project_dirs(repo: Path) -> set[str]:
    """Folders of .NET test projects (relative), whose source files are tests."""
    dirs = set()
    for p in runner.find_projects(repo):
        for c in runner._csprojs(p) if p.kind == "dotnet" else []:
            if runner._is_test_project(runner.Project(p.pack, p.kind, c)):
                dirs.add(c.parent.relative_to(repo).as_posix())
    return dirs


SMALL_REPO = 30000  # a repository this small (characters of source) is shown whole, whatever the Scout picked


def shown_files(repo: Path, files: list[str], picked: list[str] | None) -> list[str]:
    """The Scout's files first; the rest too when the repository is small (a file the Scout missed is the most
    common way to fail, and a small repository costs little to show whole)."""
    if not picked:
        return files
    size = sum((repo / f).stat().st_size for f in files if (repo / f).exists())
    first = [f for f in picked if f in files]
    return first + [f for f in files if f not in first] if size <= SMALL_REPO else first


def context_pack(repo: Path, files: list[str], protected: set[str]) -> tuple[str, list[str]]:
    """The files as the Builder sees them, and notes about what was left out."""
    parts, total, notes = [], 0, []
    for rel in files:
        try:
            text = (repo / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
        except (UnicodeDecodeError, OSError):
            continue
        if len(text) > FILE_CHARS:
            text = text[:FILE_CHARS] + "\n…(cut here)"
            notes.append(f"{rel} was cut to {FILE_CHARS} characters")
        if total + len(text) > CONTEXT_CHARS:
            notes.append(f"{len(files) - len(parts)} files were left out (too much text)")
            break
        tag = "   (client's test, read only)" if rel in protected else ""
        parts.append(f"--- {rel}{tag} ---\n```\n{text.rstrip()}\n```")
        total += len(text)
    return "\n\n".join(parts), notes


# ---- whose tests fail ---------------------------------------------------------------------------

def _names(repo: Path, rel: str) -> set[str]:
    """What a test file can be recognised by in test output: its name, and the classes it defines."""
    p = Path(rel)
    names = {p.name, p.stem.split(".")[0]}
    try:
        names |= set(re.findall(r"\bclass\s+(\w+)", (repo / rel).read_text(encoding="utf-8", errors="replace")))
    except OSError:
        pass
    return {n for n in names if len(n) > 3}


def failing_owners(repo: Path, results: list[runner.Result], client: set[str], own: set[str]) -> tuple[list[str], list[str]]:
    """(client's test files, Builder's test files) that show up in the failed tests' error lines."""
    text = "\n".join("\n".join(r.errors) + "\n" + r.output for r in results if not r.ok and r.action == "test")
    if not text:
        return [], []
    hit = lambda rel: any(re.search(rf"(?<![\w.]){re.escape(n)}\b", text) for n in _names(repo, rel))  # noqa: E731
    return sorted(f for f in client if hit(f)), sorted(f for f in own if hit(f))


# ---- one step -----------------------------------------------------------------------------------

def _checks(job: jobs.Job, ask_fn, on_event: Event) -> tuple[list[runner.Result], str | None]:
    """Build every project, then test. Returns (results, stop reason) where a stop reason means the
    step can't go on without the person (a refused restore, a missing tool)."""
    results: list[runner.Result] = []
    for action in ("build", "test"):
        rs = job.run(action, ask_fn=ask_fn)
        if any(r.state == "needs restore" for r in rs):
            on_event("Restore needed", "asking on screen before downloading packages")
            restored = job.run("restore", ask_fn=ask_fn)
            refused = [r for r in restored if not r.ok]
            if refused:
                return results + restored, ("a restore (package download) is needed and wasn't allowed, or "
                                            "failed:\n" + runner.report(refused, "restore"))
            rs = job.run(action, ask_fn=ask_fn)
        missing = [r for r in rs if r.state == "not installed"]
        if missing:
            return results + rs, missing[0].errors[0]
        results += rs
        if not all(r.ok for r in rs):
            break   # no tests on code that doesn't build
    return results, None


def _packages_in(path: Path, text: str) -> set[str]:
    """Package names a project file lists: <PackageReference Include=...> in a .csproj, dependencies in package.json."""
    if path.suffix == ".csproj":
        return {m.lower() for m in re.findall(r"<PackageReference\s+Include=\"([^\"]+)\"", text)}
    try:
        import json
        data = json.loads(text)
        return {k.lower() for sec in ("dependencies", "devDependencies", "peerDependencies") for k in data.get(sec, {})}
    except (ValueError, AttributeError):
        return set()


def package_files(repo: Path) -> dict[Path, str]:
    """Every .csproj and package.json with its text (to see what an edit added)."""
    out = {}
    for here, files in runner._walk(repo):
        for f in files:
            if f.endswith(".csproj") or f == "package.json":
                try:
                    out[here / f] = (here / f).read_text(encoding="utf-8")
                except OSError:
                    pass
    return out


def guard_packages(repo: Path, before: dict[Path, str], approved: list[tuple[str, str]]) -> list[str]:
    """Undo an edit that added a package the user hasn't approved; returns what was undone. The prompt asks for
    NEEDS_APPROVAL, but on Day 7 the Builder added an unapproved package anyway (one that didn't even exist at that
    version), and the restore that downloads packages is the step that must not run unasked."""
    allowed = {n.lower() for n, _ in approved}
    undone = []
    for path, text in package_files(repo).items():
        old = before.get(path, "")
        added = _packages_in(path, text) - _packages_in(path, old) - allowed
        if added:
            if path in before:
                path.write_text(old, encoding="utf-8")
            else:
                path.unlink()
            undone.append(f"{path.relative_to(repo).as_posix()}: {', '.join(sorted(added))}")
    return undone


def approved_packages(job: jobs.Job) -> list[tuple[str, str]]:
    """(package, version) the user approved for this job: approved-packages.txt in the job folder, one
    "Name Version" per line. Without it the Builder adds no packages (NEEDS_APPROVAL)."""
    f = job.folder / "approved-packages.txt"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        parts = line.split("#", 1)[0].split()
        if len(parts) >= 2:
            out.append((parts[0], parts[1]))
    return out


# a namespace or extension method the compiler can't find -> the package that brings it
NEEDS_PACKAGE = {"Microsoft.EntityFrameworkCore.Sqlite": ("UseSqlite", "Microsoft.Data.Sqlite", "Microsoft.EntityFrameworkCore",
                                                         "AddDbContext", "DbContext", "DbSet"),
                 "Microsoft.AspNetCore.Identity.EntityFrameworkCore": ("AddEntityFrameworkStores", "IdentityDbContext",
                                                                       "Microsoft.AspNetCore.Identity.EntityFrameworkCore"),
                 "Microsoft.EntityFrameworkCore": ("Microsoft.EntityFrameworkCore", "AddDbContext", "DbContext"),
                 "Stripe.net": ("Stripe",)}


def package_hints(text: str, approved: list[tuple[str, str]], owners: list[str]) -> list[str]:
    """For errors that mean a package isn't referenced yet: which approved package to add, and where."""
    out = []
    wanted = {f"{ns}.{n}" for n, ns in re.findall(
        r"error CS0234: The type or namespace name '(\w+)' does not exist in the namespace '([\w.]+)'", text)}
    wanted |= set(re.findall(r"no accessible extension method '(\w+)'", text))
    wanted |= set(re.findall(r"The type or namespace name '(\w+)' could not be found", text))
    for pkg, version in approved:
        triggers = NEEDS_PACKAGE.get(pkg, (pkg,))
        if any(t in wanted or any(w.startswith(t) for w in wanted) for t in triggers):
            out.append(f"the package {pkg} isn't referenced: add <PackageReference Include=\"{pkg}\" "
                       f"Version=\"{version}\" /> to {', '.join(owners) or 'the project file that needs it'} "
                       "(it is approved for this job).")
    return out


MISNAMED = {
    "AddRateLimiting": "doesn't exist; it's builder.Services.AddRateLimiter(o => o.AddFixedWindowLimiter(\"login\", ...)), built into "
                       "ASP.NET Core 8 (using Microsoft.AspNetCore.RateLimiting; System.Threading.RateLimiting); no package.",
    "UseRateLimiting": "doesn't exist; it's app.UseRateLimiter(), and [EnableRateLimiting(\"login\")] on the endpoint; no package.",
    "AddFixedWindowLimiter": "it does exist, as an extension method in Microsoft.AspNetCore.RateLimiting: add "
                             "`using Microsoft.AspNetCore.RateLimiting;` (built into ASP.NET Core 8, no package).",
}
KNOWN_TYPES = {   # library types models forget the using for, and where they live
    "Fact": "Xunit", "FactAttribute": "Xunit", "Theory": "Xunit", "TheoryAttribute": "Xunit", "InlineData": "Xunit",
    "InlineDataAttribute": "Xunit", "IClassFixture<>": "Xunit", "IClassFixture": "Xunit", "Assert": "Xunit",
    "WebApplicationFactory<>": "Microsoft.AspNetCore.Mvc.Testing", "WebApplicationFactory": "Microsoft.AspNetCore.Mvc.Testing",
    "HttpStatusCode": "System.Net", "JsonContent": "System.Net.Http.Json", "IdentityUser": "Microsoft.AspNetCore.Identity",
    "UserManager<>": "Microsoft.AspNetCore.Identity", "SignInManager<>": "Microsoft.AspNetCore.Identity",
    "RoleManager<>": "Microsoft.AspNetCore.Identity", "IdentityRole": "Microsoft.AspNetCore.Identity",
    "IdentityDbContext<>": "Microsoft.AspNetCore.Identity.EntityFrameworkCore",
    "IdentityDbContext": "Microsoft.AspNetCore.Identity.EntityFrameworkCore", "DbContext": "Microsoft.EntityFrameworkCore",
    "DbSet<>": "Microsoft.EntityFrameworkCore", "DbContextOptions<>": "Microsoft.EntityFrameworkCore",
    "DbContextOptions": "Microsoft.EntityFrameworkCore", "SqliteConnection": "Microsoft.Data.Sqlite",
    "EnableRateLimiting": "Microsoft.AspNetCore.RateLimiting", "EnableRateLimitingAttribute": "Microsoft.AspNetCore.RateLimiting",
    "FixedWindowRateLimiterOptions": "System.Threading.RateLimiting", "RateLimitPartition": "System.Threading.RateLimiting",
    "AuthorizeAttribute": "Microsoft.AspNetCore.Authorization", "Authorize": "Microsoft.AspNetCore.Authorization",
    "AllowAnonymous": "Microsoft.AspNetCore.Authorization", "AllowAnonymousAttribute": "Microsoft.AspNetCore.Authorization",
    "ControllerBase": "Microsoft.AspNetCore.Mvc", "ApiControllerAttribute": "Microsoft.AspNetCore.Mvc",
    "IActionResult": "Microsoft.AspNetCore.Mvc", "ClaimTypes": "System.Security.Claims",
    "ClaimsPrincipal": "System.Security.Claims", "RequiredAttribute": "System.ComponentModel.DataAnnotations",
    "IWebHostBuilder": "Microsoft.AspNetCore.Hosting", "IWebHostEnvironment": "Microsoft.AspNetCore.Hosting",
    "IServiceCollection": "Microsoft.Extensions.DependencyInjection", "ServiceDescriptor": "Microsoft.Extensions.DependencyInjection",
    "IServiceProvider": "System", "IServiceScope": "Microsoft.Extensions.DependencyInjection",
    "IConfiguration": "Microsoft.Extensions.Configuration", "ILogger<>": "Microsoft.Extensions.Logging",
    "ILogger": "Microsoft.Extensions.Logging", "IHost": "Microsoft.Extensions.Hosting",
    "IOptions<>": "Microsoft.Extensions.Options", "HttpClient": "System.Net.Http",
}
CS_MISSING = re.compile(r"^(.*?\.cs)\(\d+,\d+\): error CS0246: The type or namespace name '([\w<>,]+)'", re.M)


def compiler_hints(repo: Path, results: list[runner.Result]) -> list[str]:
    """For C# "type not found" errors where the type is in the repository after all: which using to add.
    Day 7: the compiler said 'ApplicationUser' could not be found, the class was in DropShip.Api.Models, and the
    Builder rewrote the file three times without the one using line it needed."""
    text = "\n".join("\n".join(r.errors) + "\n" + r.output for r in results if not r.ok)
    wanted: dict[str, set[str]] = {}
    for m in CS_MISSING.finditer(text):
        wanted.setdefault(m.group(2), set()).add(m.group(1).strip().replace("\\", "/"))
    hints = []
    sources = [p for p in wb_code.project_files(repo) if p.suffix == ".cs"]
    for name, users in sorted(wanted.items()):
        found = False
        for p in sources:
            src = p.read_text(encoding="utf-8", errors="replace")
            if re.search(rf"\b(class|record|interface|enum|struct)\s+{re.escape(name)}\b", src):
                found = True
                ns = re.search(r"^\s*namespace\s+([\w.]+)", src, re.M)
                if ns:
                    hints.append(f"{name} is defined in {p.relative_to(repo).as_posix()} in namespace {ns.group(1)}: "
                                 f"add `using {ns.group(1)};` at the top of {', '.join(sorted(users))}.")
                break
        if not found and name in KNOWN_TYPES:   # a library type: which using brings it in
            hints.append(f"{name} comes from {KNOWN_TYPES[name]}: add `using {KNOWN_TYPES[name]};` at the top of "
                         f"{', '.join(sorted(users))}.")
        elif (not found and name != "Program" and not name.endswith(("Attribute", "<>")) and name[:1].isupper()
              and not re.match(r"I[A-Z]", name)):   # IWebHostBuilder and the like are library interfaces
            # Day 7: Program.cs used ApplicationDbContext for five tries; no file ever defined it
            hints.append(f"{name} is used in {', '.join(sorted(users))} but no file in the repository defines it: "
                         f"if it is meant to be this project's own class, create it (a class named {name} in its own "
                         "file); if it is a library type, add the using for its namespace.")
        if name == "Program" and not found:   # top-level statements: tests can't see the generated Program class
            prog = next((p for p in sources if p.name == "Program.cs"), None)
            if prog:
                hints.append(f"Program has no class of its own ({prog.relative_to(repo).as_posix()} uses top-level "
                             "statements), so WebApplicationFactory<Program> can't find it: add the line "
                             "`public partial class Program { }` at the end of that file.")
    # <Using Include="X.Y" /> in a .csproj for a namespace no file declares: the error points at GlobalUsings.g.cs
    for m in re.finditer(r"GlobalUsings\.g\.cs\(\d+,\d+\): error CS0234: The type or namespace name '(\w+)' does not "
                         r"exist in the namespace '([\w.]+)'", text):
        ns = f"{m.group(2)}.{m.group(1)}"
        owner = next((p for p in wb_code.project_files(repo) if p.suffix == ".csproj"
                      and f'Include="{ns}"' in p.read_text(encoding="utf-8", errors="replace")), None)
        hints.append(f"the namespace {ns} is in a <Using Include=\"{ns}\" /> "
                     + (f"in {owner.relative_to(repo).as_posix()} " if owner else "in a .csproj ")
                     + "but no file declares it: remove that line, or put `namespace " + ns + ";` in the files meant "
                       "to be in it.")
    # a service the app needs but nobody registered (seen at start, in the tests: Day 7, DropShipDbContext)
    for svc in dict.fromkeys(re.findall(r"Unable to resolve service for type '([\w.`\[\],]+)'", text)):
        short = svc.split("`")[0].rsplit(".", 1)[-1]
        prog = next((p.relative_to(repo).as_posix() for p in sources if p.name == "Program.cs"
                     and "tests" not in p.parts), "Program.cs")
        how = (f"builder.Services.AddDbContext<{short}>(options => options.UseSqlite(...))" if short.endswith("DbContext")
               else f"builder.Services.AddScoped<{short}>() (or with its interface)")
        hints.append(f"the app can't start: nothing registers {short}. Register it in {prog} before builder.Build(): "
                     f"{how}.")
    for wrong, right in MISNAMED.items():   # methods models invent, and the real ones
        if re.search(rf"definition for '{wrong}'", text):
            hints.append(f"{wrong}: {right}")
    if re.search(r"error CS0122: 'Program' is inaccessible", text):   # the generated Program class is internal
        prog = next((p for p in sources if p.name == "Program.cs"), None)
        if prog:
            hints.append(f"Program is internal: add the line `public partial class Program {{ }}` at the end of "
                         f"{prog.relative_to(repo).as_posix()}, so the tests can use WebApplicationFactory<Program>.")
    # a ProjectReference to a path that doesn't exist: the real path, worked out from the files
    for m in re.finditer(r"warning MSB9008: The referenced project (.+?) does not exist\. \[(.+?\.csproj)\]", text):
        ref, owner = m.group(1).strip(), m.group(2).strip().replace("\\", "/")
        target = ref.replace("\\", "/").rsplit("/", 1)[-1]
        real = [p for p in wb_code.project_files(repo) if p.name == target]
        if real:
            rel = os.path.relpath(real[0], (repo / owner).parent).replace("/", "\\")
            hints.append(f"{owner} references {ref}, which doesn't exist; the project is at "
                         f"{real[0].relative_to(repo).as_posix()}, so the reference must be "
                         f"<ProjectReference Include=\"{rel}\" />.")
    # Vite without its page
    for r in results:
        if not r.ok and 'Could not resolve entry module "index.html"' in (r.output + "\n".join(r.errors)):
            folder = r.project.rsplit("/", 1)[0] if "/" in r.project else "."
            hints.append(f"Vite builds from {folder}/index.html, which doesn't exist: create it, with "
                         '<div id="root"></div> and <script type="module" src="/src/main.jsx"></script> in its body.')
    return list(dict.fromkeys(hints))


def _feedback(outcomes: list[edits.Outcome], parsed: edits.Parsed, results: list[runner.Result],
              repo: Path, client_tests: set[str], own_tests: set[str],
              approved: list[tuple[str, str]] | None = None) -> str:
    out = []
    if parsed.problems:
        out.append("Parts of your reply couldn't be read, so they were NOT applied:\n"
                   + "\n".join(f"- {p}" for p in parsed.problems))
    if parsed.notes:
        out.append("\n".join(f"- {n}" for n in parsed.notes))
    if outcomes:
        out.append("Your edits:\n" + edits.report(outcomes))
    elif not parsed.problems:
        out.append("Your reply had no === EDIT: or === FILE: blocks, so nothing was changed.")
    if not results and repo is not None and not runner.find_projects(repo):
        kinds = sorted({m for p in runner.load_packs().values() for k in p.get("kind", []) for m in k["markers"]})
        out.append("There is nothing to build or test yet: the repository has no project files (" + ", ".join(kinds)
                   + "). A step can only pass when its project builds and its tests run, so create the project files "
                     "first (for .NET a .csproj for the program and one for the tests, for a React front end "
                     "package.json with a build script), each with what it needs to build.")
    if results:
        failed = [r for r in results if not r.ok]
        if failed:
            out.append("The checks after your edits:\n" + runner.report(results, "build and test"))
            hints = compiler_hints(repo, results)
            if approved:
                text = "\n".join("\n".join(r.errors) + "\n" + r.output for r in results if not r.ok)
                owners = sorted({r.project for r in results if not r.ok and r.project.endswith(".csproj")})
                hints += package_hints(text, approved, owners)
            if hints:
                out.append("How to fix these errors (worked out from the repository's files):\n"
                           + "\n".join(f"- {h}" for h in hints))
            c, o = failing_owners(repo, results, client_tests, own_tests)
            if c:
                out.append("Failing tests in the client's files (read only; the code must make them pass): "
                           + ", ".join(c))
            if o:
                out.append("Failing tests in files you wrote: " + ", ".join(o) + ". Decide whether the test or "
                           "the code is wrong; a test of yours that expects something the step didn't ask for "
                           "is wrong.")
    return "\n\n".join(out)


def build_step(job: jobs.Job, step: str, s: Settings | None = None, *, ask_fn: runner.AskFn | None = None,
               fix_list: str = "", max_tries: int = MAX_TRIES, on_event: Event | None = None,
               commit: bool = True, pack=None, commit_message: str = "", allow_disputes: bool = True) -> BuildResult:
    """Make `step` true in the job's repository. `fix_list` = what a review found wrong last time.
    `pack` = the Scout's ContextPack (requirements, places, files), when there is one."""
    s = s or Settings.load()
    on_event = on_event or (lambda t, d: None)
    repo = job.repo.resolve()
    card = job.card()
    stack = (card.stack or ", ".join(runner.detect(repo)) or "web").split(",")[0].strip()
    notes = runner.pack_notes(stack) or "(none)"
    approved = approved_packages(job)
    rule = ("No new packages: if the step truly needs one, write only NEEDS_APPROVAL: <package and why> and stop."
            if not approved else
            "Packages already approved for this job, which you may add to a project file when the step needs them "
            "(exactly these versions, e.g. <PackageReference Include=\"Name\" Version=\"1.2.3\" />): "
            + ", ".join(f"{n} {v}" for n, v in approved)
            + ". Any other new package: write only NEEDS_APPROVAL: <package and why> and stop.")
    system = PROMPT.replace("{stack}", stack).replace("{stack_notes}", notes).replace("{packages_rule}", rule)
    start_files = set(repo_files(repo))
    # the client's tests are those in the code as received; tests from earlier steps are the Builder's own
    received = set(job.client_files())
    client_tests = edits.protected_tests(repo, sorted(received), test_project_dirs(repo))
    replies = job.folder / "builder"
    replies.mkdir(exist_ok=True)
    step_no = len(list(replies.glob("step-*-try-1.md"))) + 1
    job.log(f"Builder · step {step_no} · {step[:80]}",
            f"model {wb_ai.expert_model(s, 'code')}\nclient's tests (read only): "
            + (", ".join(sorted(client_tests)) or "none"))

    result = BuildResult("failed", step)
    feedback = f"A review of the last attempt found:\n{fix_list}" if fix_list else ""
    ever_applied = False
    last_reply = ""
    for n in range(1, max_tries + 1):
        files = repo_files(repo)
        shown = shown_files(repo, files, pack.files if pack else None)
        text, ctx_notes = context_pack(repo, shown, client_tests)
        hidden = [f for f in files if f not in shown]
        if hidden:
            ctx_notes.append("not shown (the Scout didn't pick them): " + ", ".join(hidden[:40]))
        own_tests = {f for f in files if f not in received and edits.looks_like_test(f, test_project_dirs(repo))}
        request = job.request()
        user = (f"Job: {card.title}\n" + (f"Goal: {card.goal}\n" if card.goal else "")
                + (f"\nFor reference only, the job's whole request (take names, numbers and details from it; the "
                   f"rest of it is OTHER steps, done before or after this one):\n{request}\n" if request else "")
                + f"\nYour step: do ONLY this, nothing else from the request:\n{step}\n"
                + (f"\n{pack.for_prompt()}\n" if pack and pack.requirements else "")
                + f"\nThe repository's files (current state):\n\n{text}"
                + (f"\n\n({'; '.join(ctx_notes)})" if ctx_notes else "")
                + (f"\n\nThis is try {n} of {max_tries}. What happened on the last try:\n\n{feedback}" if feedback else ""))
        on_event(f"Try {n} of {max_tries}", f"asking {wb_ai.expert_model(s, 'code')} for edits")
        started = time.monotonic()
        try:
            a = wb_ai.ask(s, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                          task="code", expert="code", big=False, temperature=0.2, max_tokens=8000)
            reply = a.text
        except Exception as e:  # noqa: BLE001 - a model crash is a failed try, written to the log
            reply = ""
            job.log(f"Builder · try {n} · the model failed", f"{type(e).__name__}: {e}")
        seconds = time.monotonic() - started
        (replies / f"step-{step_no}-try-{n}.md").write_text(reply, encoding="utf-8")
        rec = TryRecord(n, round(seconds))
        result.tries.append(rec)

        parsed = edits.parse(reply)
        if len(parsed.edits) > MAX_EDITS:
            parsed.problems.append(f"the reply had {len(parsed.edits)} edit blocks, so it ran away (repeated "
                                   "itself) and nothing was applied. Write each change once, in a few blocks")
            parsed.edits = []
        if parsed.disputes and not allow_disputes:
            parsed.problems.append("DISPUTE isn't accepted in this round: the Checker has read your dispute and "
                                   "still finds the problem. Change the code")
            parsed.disputes = []
        if parsed.needs_approval:
            result.status = "ask"
            result.message = "The Builder needs a new package, which needs your OK:\n" + "\n".join(
                f"- {x}" for x in parsed.needs_approval)
            job.log(f"Builder · try {n} · asks for approval", result.message)
            break
        before = package_files(repo)
        outcomes = edits.apply(repo, parsed.edits, client_tests, received)
        for line in guard_packages(repo, before, approved):
            rel, _, names = line.partition(": ")
            for o in outcomes:
                if o.ok and o.edit.path.replace("\\", "/").lstrip("./") == rel:
                    o.ok, o.why = False, (f"adds packages that aren't approved for this job ({names}), so the file "
                                          "was put back as it was. Use what the project already has, or write "
                                          "NEEDS_APPROVAL: <package and why>")
            job.log(f"Builder · try {n} · unapproved package undone", line)
        rec.applied = [o.edit.path for o in outcomes if o.ok]
        rec.refused = [f"{o.edit.path}: {o.why.splitlines()[0]}" for o in outcomes if not o.ok]
        ever_applied |= bool(rec.applied)
        if fix_list and parsed.disputes:
            result.disputes = parsed.disputes   # the last try's disputes go to the Checker's next round
        # in a FIX round, disputing every item (no edits) is a real answer: the Checker decides on it next
        # "the code already does this" is a real answer too (an earlier step did the work: Day 7, where a model that
        # had nothing left to change looped until its answer ran out); the Checker then judges it like any other
        answered = ever_applied or bool(fix_list and parsed.disputes) or bool(parsed.already_done)
        if parsed.already_done:
            result.already_done = parsed.already_done
            job.log(f"Builder · try {n} · says the step is already done", parsed.already_done)
        on_event(f"Try {n}: {len(rec.applied)} edits applied, {len(rec.refused)} refused"
                 + (f", {len(parsed.disputes)} disputed" if parsed.disputes else ""), "building and testing")
        results, stop = _checks(job, ask_fn, on_event) if answered else ([], None)
        rec.checks = runner.report(results, "build and test") if results else ""
        job.log(f"Builder · try {n} · {seconds:.0f} s · {len(rec.applied)} applied, {len(rec.refused)} refused"
                + (f", {len(parsed.disputes)} disputed" if parsed.disputes else ""),
                edits.report(outcomes) + "".join(f"\nDISPUTE: {d}" for d in parsed.disputes)
                + (f"\n\n{rec.checks}" if rec.checks else ""))
        if stop:
            result.status, result.message = "ask", stop
            break
        unfinished = edits.placeholders(outcomes)
        # a refused edit is a change the Builder meant and didn't get: no pass on top of it (Day 4: a refused fix
        # plus two disputes counted as done). Refusing an edit to the client's tests is meant, and doesn't count.
        missed = [o for o in outcomes if not o.ok and o.edit.path not in client_tests]
        # a block that couldn't be read is a change the Builder meant and didn't get: never a pass on top of it
        # (Day 6: a new file it wrote in a form the parser didn't know was dropped, and the rest built and passed)
        rec.ok = (answered and bool(results) and all(r.ok for r in results) and not unfinished and not missed
                  and not parsed.problems)
        if rec.ok:
            result.status = "pass"
            break
        # Day 1 lesson, seen again on Day 5: the Builder's own new test expects the wrong thing (1200 copper as
        # "1s 20c"), and it bends correct code to fit, or never passes. After one try to sort it out itself, a
        # step where everything builds and only its own tests fail goes to the Checker, which decides whether the
        # test or the code is wrong.
        if answered and results and not unfinished and not missed and not parsed.problems and n >= 2:
            builds_ok = all(r.ok for r in results if r.action == "build")
            c, o = failing_owners(repo, results, client_tests, own_tests)
            if builds_ok and o and not c:
                result.status, result.own_failing = "pass", o
                job.log(f"Builder · try {n} · only its own tests fail: the Checker decides", ", ".join(o))
                break
        feedback = _feedback(outcomes, parsed, results, repo, client_tests, own_tests, approved)
        if reply.strip() and reply.strip() == last_reply:
            # Day 7: three tries in a row with the same answer while the build errors stayed; say it plainly
            feedback = ("Your answer was the same as on the last try, and it changed nothing. Read the errors below and "
                        "fix those first, in the files they name.\n\n" + feedback)
        last_reply = reply.strip()
        if unfinished:
            feedback += ("\n\nYour edits left work undone (placeholder lines). Write the real code instead:\n"
                         + "\n".join(f"- {u}" for u in unfinished))
            job.log(f"Builder · try {n} · placeholders", "\n".join(unfinished))

    now = set(repo_files(repo))
    result.new_files = sorted(now - start_files)
    changed = (jobs.git(repo, "diff", "--name-only", "HEAD", check=False) + "\n"
               + jobs.git(repo, "ls-files", "--others", "--exclude-standard", check=False))
    result.changed_files = sorted({ln.strip() for ln in changed.splitlines() if ln.strip()})
    if result.ok:
        result.message = (f"Step done on try {len(result.tries)}: it builds and the tests pass.\n"
                          if not result.own_failing else
                          f"Step built on try {len(result.tries)}; only tests the Builder wrote fail "
                          f"({', '.join(result.own_failing)}): the Checker decides whether the test or the code is "
                          "wrong.\n") + result.tries[-1].checks
        if commit and result.changed_files:
            jobs.git(repo, "add", "-A")
            jobs.git(repo, "-c", "user.name=AI Workbench Builder", "-c", "user.email=workbench@localhost",
                     "commit", "--quiet", "-m", commit_message or f"Builder: {step.splitlines()[0][:72]}")
            result.commit = jobs.git(repo, "rev-parse", "--short", "HEAD")
    elif result.status == "failed":
        last = result.tries[-1] if result.tries else None
        result.message = (f"Stopped after {len(result.tries)} tries without a passing build and tests. "
                          "The changes are left in the repository (not committed) for you or the next round.\n"
                          + ((last.checks or "\n".join(last.refused)) if last else ""))
    job.log(f"Builder · step {step_no} · {result.status}" + (f" · commit {result.commit}" if result.commit else ""),
            result.message)
    return result
