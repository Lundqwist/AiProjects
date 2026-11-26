"""Tests for the Builder's edits (team\\edits.py) and its try loop (team\\builder.py), with a fake model.

Run: .venv\\Scripts\\python.exe app\\test_builder.py      (needs node and git; no models, no network)
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wb_ai  # noqa: E402
from team import builder, edits, jobs, runner  # noqa: E402
from wb_settings import Settings  # noqa: E402

failures: list[str] = []
TMP = Path(tempfile.mkdtemp(prefix="wb-builder-test-"))
F = "`" * 3


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(f"{name}: {detail}")


# ---- parsing ------------------------------------------------------------------------------------

reply = f"""I add the endpoint and a helper.

=== EDIT: src/app.js ===
<<<<<<< SEARCH
const a = 1;
=======
const a = 2;
>>>>>>> REPLACE

<<<<<<< SEARCH
{F}js
function f() {{
  return a;
}}
{F}
=======
{F}js
function f() {{
  return a + 1;
}}
{F}
>>>>>>> REPLACE

### EDIT: `src/other.js`
<<<<<<< SEARCH
x
=======
y
>>>>>>> REPLACE

=== FILE: src/new.js ===
{F}js
export const n = 1;
{F}

RUN: test
"""
p = edits.parse(reply)
check("parses edits (two under one header), a ### header and a new file",
      [(e.path, e.new_file) for e in p.edits] == [("src/app.js", False), ("src/app.js", False),
                                                  ("src/other.js", False), ("src/new.js", True)],
      str([(e.path, e.new_file) for e in p.edits]))
check("``` around SEARCH and REPLACE is dropped", p.edits[1].search.startswith("function f()")
      and p.edits[1].replace.endswith("}"), repr(p.edits[1].search))
check("RUN: is read", p.run == ["test"])
check("a new file's content is the code block", p.edits[3].replace == "export const n = 1;\n")
p = edits.parse("<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE\n=== FILE: x.js ===\n```js\ncut off")
check("a block without a header and a cut-off file are reported, not guessed",
      not p.edits and len(p.problems) == 2, str(p.problems))
block = "=== EDIT: a.js ===\n<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE\n"
p = edits.parse(block * 3 + "=== EDIT: a.js ===\n<<<<<<< SEARCH\nz\n=======\nz\n>>>>>>> REPLACE\n"
                "DISPUTE: 2 - a.js:4 already returns 0\n")
check("repeated blocks and blocks that change nothing are dropped and noted (not problems); DISPUTE is read",
      len(p.edits) == 1 and len(p.notes) == 2 and not p.problems and p.disputes == ["2 - a.js:4 already returns 0"],
      f"{len(p.edits)} {p.notes} {p.problems} {p.disputes}")
p = edits.parse("=== FILE: src/Boot.java ===\n<<<<<<< SEARCH\nclass Boot { // TODO }\n=======\nclass Boot {\n  int x;\n}\n"
                ">>>>>>> REPLACE\n")
check("=== FILE: followed by SEARCH/REPLACE is read as a new file written as an edit",
      len(p.edits) == 1 and p.edits[0].create_if_missing and not p.problems, f"{p.edits} {p.problems}")
fresh = TMP / "fresh"
fresh.mkdir()
out = edits.apply(fresh, p.edits)
check("... and creates the file from REPLACE when it doesn't exist",
      out[0].ok and (fresh / "src" / "Boot.java").read_text(encoding="utf-8") == "class Boot {\n  int x;\n}\n",
      edits.report(out))
check("NEEDS_APPROVAL is read", edits.parse("NEEDS_APPROVAL: dayjs for date maths").needs_approval
      == ["dayjs for date maths"])
check("ALREADY_DONE next to edits is ignored (the edits are the answer)",
      edits.parse("=== EDIT: a.js ===\n<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE\nALREADY_DONE: a.js:1").already_done == "")

# ---- applying -----------------------------------------------------------------------------------

repo = TMP / "r"
(repo / "src").mkdir(parents=True)
(repo / "test").mkdir()
crlf = "class A\r\n{\r\n    void F()\r\n    {\r\n        Do(1);\r\n    }\r\n}\r\n"
(repo / "src" / "A.cs").write_bytes(b"\xef\xbb\xbf" + crlf.encode())
(repo / "src" / "b.js").write_text("let x = 1;\nlet x = 1;\n", encoding="utf-8")
(repo / "test" / "a.test.js").write_text("test()\n", encoding="utf-8")
(repo / ".env").write_text("SECRET=1\n", encoding="utf-8")

E = edits.Edit
out = edits.apply(repo, [E("src/A.cs", "        Do(1);", "        Do(2);\n        Do(3);")])
data = (repo / "src" / "A.cs").read_bytes()
check("an exact edit applies and keeps CRLF and the BOM",
      out[0].ok and data.startswith(b"\xef\xbb\xbf") and b"Do(2);\r\n        Do(3);\r\n" in data, repr(data))
out = edits.apply(repo, [E("src/A.cs", "void F()\n{\n    Do(2);", "void G()\n{\n    Do(9);")])
text = (repo / "src" / "A.cs").read_bytes().decode("utf-8-sig")
check("a SEARCH with other indentation still fits, and the replacement is indented like the file",
      out[0].ok and "    void G()\r\n    {\r\n        Do(9);" in text, repr(text))
out = edits.apply(repo, [E("src/A.cs", "Do(42);", "Do(43);")])
check("a SEARCH that isn't there is refused, with the file's nearest lines",
      not out[0].ok and "isn't in the file" in out[0].why and "Do(9);" in out[0].why, out[0].why)
out = edits.apply(repo, [E("src/b.js", "let x = 1;", "let y = 1;")])
check("a SEARCH that fits two places is refused", not out[0].ok and "more than one place" in out[0].why)
protected = edits.protected_tests(repo, ["src/A.cs", "src/b.js", "test/a.test.js"], set())
check("the client's tests are found", protected == {"test/a.test.js"}, str(protected))
out = edits.apply(repo, [E("test/a.test.js", "test()", "skip()"), E(".env", "SECRET=1", "x"),
                         E("../outside.txt", "", "x", True), E("C:/Windows/x.txt", "", "x", True),
                         E(".git/config", "", "x", True)], protected)
check("client's tests, secrets, outside paths and .git are refused",
      [o.ok for o in out] == [False] * 5 and "client's existing tests" in out[0].why
      and (repo / "test" / "a.test.js").read_text() == "test()\n" and not (TMP / "outside.txt").exists(),
      edits.report(out))
out = edits.apply(repo, [E("test/more.test.js", "", "test2()\n", True), E("src/A.cs", "", "x", True)], protected)
check("a new test file is fine; a whole-file rewrite of an existing file is too, if it is small",
      out[0].ok and out[1].ok and (repo / "src" / "A.cs").read_text(encoding="utf-8-sig") == "x", edits.report(out))
(repo / "src" / "big.js").write_text("\n".join(f"line{i}" for i in range(100)), encoding="utf-8")
out = edits.apply(repo, [E("src/big.js", "", "gone", True)])
check("a whole-file rewrite of a big existing file is refused", not out[0].ok and "EDIT" in out[0].why)
out = edits.apply(repo, [E("src/big.js", "", "rewritten\n", True)], received={"src/A.cs"})
check("... unless the team made that file itself (not in the code as received)",
      out[0].ok and (repo / "src" / "big.js").read_text(encoding="utf-8") == "rewritten\n", out[0].why)
out = edits.apply(repo, [E("src/missing.js", "made-up old lines", "export const m = 1;")])
check("an edit to a file that doesn't exist creates it from REPLACE",
      out[0].ok and (repo / "src" / "missing.js").read_text(encoding="utf-8").strip() == "export const m = 1;", out[0].why)
out = edits.apply(repo, [E("lib/b.js", "let x = 1;", "let y = 1;")])
check("... unless a file of that name is elsewhere: then it asks whether that was meant",
      not out[0].ok and "did you mean src/b.js" in out[0].why and not (repo / "lib" / "b.js").exists(), out[0].why)

O = edits.Outcome
found = edits.placeholders([
    O(E("a.cs", "    return x;", "    // In a real app, we'd remove it from the store\n    return x;"), True),
    O(E("b.html", "<p>", "<input placeholder=\"Your name\">\n<p>"), True),
    O(E("c.js", "// TODO: old note\nf();", "// TODO: old note\ng();"), True),
    O(E("d.cs", "", "throw new NotImplementedException();", True), True),
    O(E("e.cs", "x", "// TODO later"), False),
    O(E("f.cs", "x", "var todo = store.Find(id);\nreturn todo is null ? Results.NotFound() : Results.Ok(todo);"), True)])
check("placeholders in new lines are found; HTML placeholder=, old TODOs and refused edits are not",
      found == ["a.cs: // In a real app, we'd remove it from the store", "d.cs: throw new NotImplementedException();"],
      str(found))

cs = TMP / "cs"
(cs / "Models").mkdir(parents=True)
(cs / "Models" / "AppUser.cs").write_text("namespace Shop.Models;\npublic class AppUser { }\n", encoding="utf-8")
res = runner.Result("build", "Shop.csproj", "dotnet build", False, 1, 1.0,
                    "Auth\\AuthController.cs(11,40): error CS0246: The type or namespace name 'AppUser' could not be "
                    "found (are you missing a using directive or an assembly reference?)\n"
                    "Auth\\AuthController.cs(3,7): error CS0246: The type or namespace name 'IdentityUser' could not be found")
hints = builder.compiler_hints(cs, [res])
check("a C# type the compiler can't find, but the repository has, gets its using line; known library types too",
      hints == ["AppUser is defined in Models/AppUser.cs in namespace Shop.Models: add `using Shop.Models;` at the top "
                "of Auth/AuthController.cs.",
                "IdentityUser comes from Microsoft.AspNetCore.Identity: add `using Microsoft.AspNetCore.Identity;` at the "
                "top of Auth/AuthController.cs."], str(hints))

# ---- the try loop, with a fake model ------------------------------------------------------------

src = TMP / "client"
(src / "src").mkdir(parents=True)
(src / "test").mkdir()
(src / "package.json").write_text(json.dumps({"name": "c", "version": "1.0.0", "private": True,
                                              "scripts": {"test": "node --test"}}), encoding="utf-8")
(src / "src" / "sum.js").write_text("exports.sum = (a, b) => a - b;\n", encoding="utf-8")
(src / "test" / "sum.test.js").write_text(
    "const test = require('node:test');\nconst assert = require('node:assert');\nconst { sum } = require('../src/sum');\n"
    "test('adds', () => assert.strictEqual(sum(2, 3), 5));\n", encoding="utf-8")
job = jobs.create(TMP / "Jobs", "Test", "Fix sum", str(src))

answers = [
    # try 1: a SEARCH that doesn't match, plus an edit to the client's test
    "=== EDIT: src/sum.js ===\n<<<<<<< SEARCH\nexports.sum = (a, b) => a + b;\n=======\nx\n>>>>>>> REPLACE\n"
    "=== EDIT: test/sum.test.js ===\n<<<<<<< SEARCH\nsum(2, 3), 5\n=======\nsum(2, 3), -1\n>>>>>>> REPLACE\nRUN: test",
    # try 2: right file, wrong fix
    "=== EDIT: src/sum.js ===\n<<<<<<< SEARCH\n(a, b) => a - b\n=======\n(a, b) => a * b\n>>>>>>> REPLACE\nRUN: test",
    # try 3: the fix, and a test of its own
    "=== EDIT: src/sum.js ===\n<<<<<<< SEARCH\n(a, b) => a * b\n=======\n(a, b) => a + b\n>>>>>>> REPLACE\n"
    "=== FILE: test/sum-more.test.js ===\n```js\nconst test = require('node:test');\nconst assert = "
    "require('node:assert');\nconst { sum } = require('../src/sum');\ntest('zero', () => assert.strictEqual(sum(0, 0), 0));\n```\nRUN: test",
]
prompts: list[str] = []


def fake_ask(s, messages, **kw):
    prompts.append(messages[-1]["content"])
    return wb_ai.Answer(answers[len(prompts) - 1], "fake", "here")


real_ask, wb_ai.ask = wb_ai.ask, fake_ask
try:
    s = Settings()
    s.online_mode = "off"
    r = builder.build_step(job, "Make sum add its arguments", s)
finally:
    wb_ai.ask = real_ask

check("the Builder passes on try 3 and commits", r.ok and len(r.tries) == 3 and bool(r.commit),
      f"{r.status} {len(r.tries)} {r.message}")
check("try 1's refusals reach try 2: SEARCH not found (with the real line) and the client's test",
      "isn't in the file" in prompts[1] and "a - b" in prompts[1] and "client's existing tests" in prompts[1],
      prompts[1][-1500:])
check("the client's test is marked read only in what the Builder sees", "sum.test.js   (client's test, read only)"
      in prompts[0])
check("try 2's failing test reaches try 3, named as the client's",
      "FAILED" in prompts[2] and "Failing tests in the client's files" in prompts[2] and "sum.test.js" in prompts[2],
      prompts[2][-1500:])
check("the client's test is unchanged", (job.repo / "test" / "sum.test.js").read_text(encoding="utf-8").count("5));") == 1)
check("the commit holds the fix and the new test",
      set(jobs.git(job.repo, "show", "--name-only", "--format=", "HEAD").split()) == {"src/sum.js", "test/sum-more.test.js"})
check("each try's reply is kept in the job folder", len(list((job.folder / "builder").glob("step-1-try-*.md"))) == 3)

# a step that never gets there stops after the tries and leaves the work uncommitted
answers = ["=== EDIT: src/sum.js ===\n<<<<<<< SEARCH\n(a, b) => a + b\n=======\n(a, b) => 1\n>>>>>>> REPLACE"] * 2
prompts.clear()
wb_ai.ask = fake_ask
try:
    r = builder.build_step(job, "Break it", s, max_tries=2)
finally:
    wb_ai.ask = real_ask
check("a step that doesn't pass stops after its tries, not committed",
      r.status == "failed" and len(r.tries) == 2 and not r.commit and "src/sum.js" in r.changed_files, r.message)
check("its own new test failing is named as the Builder's", "files you wrote: test/sum-more.test.js" in prompts[1],
      prompts[1][-800:])

# a FIX round: an edit that misses plus disputes is not a pass; disputes in a later round aren't accepted
jobs.git(job.repo, "checkout", "-q", "--", ".")
miss = ("=== EDIT: src/sum.js ===\n<<<<<<< SEARCH\nnot in the file\n=======\nx\n>>>>>>> REPLACE\n"
        "DISPUTE: 1 - src/sum.js:1 already adds\nRUN: test")
answers = [miss, miss]
prompts.clear()
wb_ai.ask = fake_ask
try:
    r = builder.build_step(job, "Fix it", s, max_tries=2, fix_list="1. src/sum.js:1 - wrong")
finally:
    wb_ai.ask = real_ask
check("a refused edit plus a dispute is not a pass", r.status == "failed" and len(r.tries) == 2, r.status)
answers = ["DISPUTE: 1 - src/sum.js:1 already adds\nRUN: test"]
prompts.clear()
wb_ai.ask = fake_ask
try:
    r = builder.build_step(job, "Fix it", s, max_tries=1, fix_list="1. src/sum.js:1 - wrong")
    r2_answers_ok = r.ok and r.disputes == ["1 - src/sum.js:1 already adds"]
    answers = ["DISPUTE: 1 - src/sum.js:1 already adds\nRUN: test"]
    prompts.clear()
    r2 = builder.build_step(job, "Fix it", s, max_tries=1, fix_list="1. src/sum.js:1 - wrong", allow_disputes=False)
finally:
    wb_ai.ask = real_ask
check("disputing every item, with no edits, answers a first FIX round (the Checker decides next)", r2_answers_ok,
      f"{r.status} {r.disputes}")
check("a dispute in a later round isn't accepted", r2.status == "failed" and not r2.disputes, r2.status)
many = "".join(f"=== EDIT: src/sum.js ===\n<<<<<<< SEARCH\nline{i}\n=======\nx{i}\n>>>>>>> REPLACE\n" for i in range(40))
answers = [many]
prompts.clear()
wb_ai.ask = fake_ask
try:
    r = builder.build_step(job, "Runaway", s, max_tries=1)
finally:
    wb_ai.ask = real_ask
check("a runaway reply (40 blocks) applies nothing", r.status == "failed" and not r.tries[0].applied, r.message)

# correct code, but its own new test expects the wrong thing: after one more try, the Checker decides
jobs.git(job.repo, "checkout", "-q", "--", ".")
jobs.git(job.repo, "clean", "-qfd")
(job.repo / "src" / "sum.js").write_text("exports.sum = (a, b) => a - b;\n", encoding="utf-8")
wrong_test = ("=== FILE: test/sum-wrong.test.js ===\n```js\nconst test = require('node:test');\nconst assert = "
              "require('node:assert');\nconst { sum } = require('../src/sum');\n"
              "test('wrong expectation', () => assert.strictEqual(sum(1, 1), 3));\n```\nRUN: test")
fix_code = "=== EDIT: src/sum.js ===\n<<<<<<< SEARCH\n(a, b) => a - b\n=======\n(a, b) => a + b\n>>>>>>> REPLACE\n"
answers = [fix_code + wrong_test, "Both are right.\nRUN: test"]
prompts.clear()
wb_ai.ask = fake_ask
try:
    r = builder.build_step(job, "Make sum add", s, max_tries=3)
finally:
    wb_ai.ask = real_ask
check("only its own tests failing: after one more try the step goes to the Checker, committed",
      r.ok and r.own_failing == ["test/sum-wrong.test.js"] and len(r.tries) == 2 and bool(r.commit),
      f"{r.status} {r.own_failing} {len(r.tries)}")

# the step is already true: ALREADY_DONE, nothing to change, the tests pass
answers = ["The step is already true.\nALREADY_DONE: src/sum.js:1 sum adds\nRUN: test"]
prompts.clear()
jobs.git(job.repo, "rm", "-q", "test/sum-wrong.test.js")
jobs.git(job.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "own wrong test gone")
wb_ai.ask = fake_ask
try:
    r = builder.build_step(job, "Make sum add", s, max_tries=1)
finally:
    wb_ai.ask = real_ask
check("ALREADY_DONE with nothing to change and passing tests is an answer (for the Checker to judge)",
      r.ok and r.already_done == "src/sum.js:1 sum adds" and not r.commit, f"{r.status} {r.already_done!r} {r.message}")
answers = ["=== EDIT: src/sum.js ===\n<<<<<<< SEARCH\n(a, b) => a + b\n=======\n(a, b) => a + b\n>>>>>>> REPLACE\nRUN: test"]
prompts.clear()
wb_ai.ask = fake_ask
try:
    r = builder.build_step(job, "Make sum add", s, max_tries=1)
finally:
    wb_ai.ask = real_ask
check("edits that only repeat code already in the file count as ALREADY_DONE (for the Checker to judge)",
      r.ok and "already in src/sum.js" in r.already_done and not r.commit, f"{r.status} {r.already_done!r}")
answers = ["=== EDIT: src/sum.js ===\n<<<<<<< SEARCH\n(a, b) => a * 2\n=======\n(a, b) => a * 2\n>>>>>>> REPLACE\nRUN: test"]
prompts.clear()
wb_ai.ask = fake_ask
try:
    r = builder.build_step(job, "Make sum add", s, max_tries=1)
finally:
    wb_ai.ask = real_ask
check("... but not when the repeated code isn't in the file", not r.ok and not r.already_done, f"{r.status}")

# a new project: code without a project file can't be built, and the Builder is told so
empty_src = TMP / "empty"
empty_src.mkdir()
(empty_src / "BRIEF.md").write_text("# A new shop\n", encoding="utf-8")
new_job = jobs.create(TMP / "Jobs", "Test", "New shop", str(empty_src))
answers = ["=== FILE: src/Shop/Program.cs ===\n```csharp\nConsole.WriteLine(1);\n```\nRUN: build",
           "=== FILE: src/Shop/Other.cs ===\n```csharp\nclass Other { }\n```\nRUN: build"]
prompts.clear()
wb_ai.ask = fake_ask
try:
    r = builder.build_step(new_job, "Make the program", s, max_tries=2)
finally:
    wb_ai.ask = real_ask
check("in a repository with no project file, a try can't pass and the next try is told to create one",
      r.status == "failed" and "nothing to build or test yet" in prompts[1] and ".csproj" in prompts[1],
      prompts[1][-700:] if len(prompts) > 1 else r.message)

(cs / "src" / "Shop.Api").mkdir(parents=True)
(cs / "src" / "Shop.Api" / "Shop.Api.csproj").write_text("<Project Sdk=\"Microsoft.NET.Sdk.Web\"></Project>", encoding="utf-8")
(cs / "src" / "Shop.Api" / "Program.cs").write_text("var app = WebApplication.Create();\napp.Run();\n", encoding="utf-8")
(cs / "tests" / "Shop.Tests").mkdir(parents=True)
(cs / "tests" / "Shop.Tests" / "Shop.Tests.csproj").write_text("<Project></Project>", encoding="utf-8")
res2 = runner.Result("build", "tests/Shop.Tests/Shop.Tests.csproj", "dotnet build", False, 1, 1.0,
                     "C:\\dotnet\\Microsoft.Common.CurrentVersion.targets(2205,5): warning MSB9008: The referenced "
                     "project ..\\Shop.Api\\Shop.Api.csproj does not exist. [tests\\Shop.Tests\\Shop.Tests.csproj]\n"
                     "tests\\Shop.Tests\\ApiTests.cs(6,67): error CS0246: The type or namespace name 'Program' could not be found")
res3 = runner.Result("build", "client/package.json", "npm run build", False, 1, 1.0,
                     'error during build:\nRollupError: Could not resolve entry module "index.html".')
res4 = runner.Result("build", "tests/Shop.Tests/Shop.Tests.csproj", "dotnet build", False, 1, 1.0,
                     "tests\\Shop.Tests\\T.cs(13,33): error CS0246: The type or namespace name 'IClassFixture<>' could "
                     "not be found\ntests\\Shop.Tests\\T.cs(22,6): error CS0246: The type or namespace name 'Fact' could "
                     "not be found\ntests\\Shop.Tests\\T.cs(13,69): error CS0122: 'Program' is inaccessible due to its "
                     "protection level")
lib = builder.compiler_hints(cs, [res4])
check("library types get their using (xUnit), and an internal Program the public partial line",
      any("IClassFixture<> comes from Xunit" in h for h in lib) and any("Fact comes from Xunit" in h for h in lib)
      and any("Program is internal" in h for h in lib), "\n".join(lib))
approved = [("Microsoft.EntityFrameworkCore.Sqlite", "8.0.11"),
            ("Microsoft.AspNetCore.Identity.EntityFrameworkCore", "8.0.11"), ("Stripe.net", "53.2.0")]
ph = builder.package_hints(
    "src\\A\\Program.cs(2,17): error CS0234: The type or namespace name 'EntityFrameworkCore' does not exist in the "
    "namespace 'Microsoft' (are you missing an assembly reference?)\nsrc\\A\\Program.cs(10,2): error CS1061: "
    "'IdentityBuilder' does not contain a definition for 'AddEntityFrameworkStores' and no accessible extension method "
    "'AddEntityFrameworkStores' accepting a first argument", approved, ["src/A/A.csproj"])
check("a missing namespace or extension method points to the approved package that brings it (not Stripe)",
      any("Microsoft.EntityFrameworkCore.Sqlite\" Version=\"8.0.11\"" in h for h in ph)
      and any("Identity.EntityFrameworkCore\" Version=\"8.0.11\"" in h for h in ph)
      and not any("Stripe" in h for h in ph), "\n".join(ph))
(cs / "src" / "Shop.Api" / "Shop.Api.csproj").write_text(
    '<Project Sdk="Microsoft.NET.Sdk.Web"><ItemGroup><Using Include="Shop.Api.Data" /></ItemGroup></Project>', encoding="utf-8")
res5 = runner.Result("build", "src/Shop.Api/Shop.Api.csproj", "dotnet build", False, 1, 1.0,
                     "src\\Shop.Api\\Program.cs(11,31): error CS0246: The type or namespace name 'ShopDbContext' could "
                     "not be found\nsrc\\Shop.Api\\obj\\Debug\\net8.0\\Shop.Api.GlobalUsings.g.cs(2,27): error CS0234: "
                     "The type or namespace name 'Data' does not exist in the namespace 'Shop.Api' (are you missing an "
                     "assembly reference?)")
res6 = runner.Result("test", "tests/Shop.Tests/Shop.Tests.csproj", "dotnet test", False, 1, 1.0,
                     "System.AggregateException : Some services are not able to be constructed (... Unable to resolve "
                     "service for type 'Shop.Api.Data.ShopDbContext' while attempting to activate 'UserStore`9[...]'.)")
di = builder.compiler_hints(cs, [res6])
check("a service nobody registered gets its registration, in the API's Program.cs",
      any("nothing registers ShopDbContext" in h and "AddDbContext<ShopDbContext>" in h and "src/Shop.Api/Program.cs" in h
          for h in di), "\n".join(di))
more = builder.compiler_hints(cs, [res5])
check("a type no file defines gets 'create it'; a <Using> for a namespace nobody declares is pointed out",
      any("ShopDbContext is used in src/Shop.Api/Program.cs but no file" in h for h in more)
      and any('<Using Include="Shop.Api.Data" /> in src/Shop.Api/Shop.Api.csproj' in h for h in more), "\n".join(more))
hints = builder.compiler_hints(cs, [res2, res3])
check("setup errors get their fix worked out: the real ProjectReference path, partial Program, Vite's index.html",
      any('<ProjectReference Include="..\\..\\src\\Shop.Api\\Shop.Api.csproj" />' in h for h in hints)
      and any("public partial class Program { }" in h and "src/Shop.Api/Program.cs" in h for h in hints)
      and any("client/index.html" in h for h in hints), "\n".join(hints))

# packages: an approved one may be added, any other is undone and blocks the pass
pk_src = TMP / "pk"
pk_src.mkdir()
(pk_src / "package.json").write_text(json.dumps({"name": "p", "version": "1.0.0", "private": True,
                                                 "scripts": {"test": "node --test"}, "dependencies": {}}), encoding="utf-8")
(pk_src / "a.test.js").write_text("require('node:test')('ok', () => {});\n", encoding="utf-8")
pk_job = jobs.create(TMP / "Jobs", "Test", "Packages", str(pk_src))
(pk_job.folder / "approved-packages.txt").write_text("dayjs 1.11.13\n", encoding="utf-8")
add_both = ('=== EDIT: package.json ===\n<<<<<<< SEARCH\n"dependencies": {}\n=======\n'
            '"dependencies": {"dayjs": "1.11.13", "left-pad": "1.3.0"}\n>>>>>>> REPLACE\nRUN: test')
add_ok = ('=== EDIT: package.json ===\n<<<<<<< SEARCH\n"dependencies": {}\n=======\n'
          '"dependencies": {"dayjs": "1.11.13"}\n>>>>>>> REPLACE\nRUN: test')
answers = [add_both]
prompts.clear()
systems: list[str] = []
wb_ai.ask = lambda s_, m, **k: (systems.append(m[0]["content"]), fake_ask(s_, m, **k))[1]
try:
    r = builder.build_step(pk_job, "Format dates", s, max_tries=1)
finally:
    wb_ai.ask = real_ask
pj = json.loads((pk_job.repo / "package.json").read_text(encoding="utf-8"))
check("an unapproved package (left-pad) is undone, with its file, and the try can't pass",
      r.status == "failed" and pj["dependencies"] == {} and any("aren't approved" in x for x in r.tries[0].refused),
      f"{r.status} {pj} {r.tries[0].refused}")
check("the Builder is told which packages it may add", bool(systems) and "dayjs 1.11.13" in systems[0])
answers = [add_ok]
prompts.clear()
wb_ai.ask = fake_ask
try:
    r = builder.build_step(pk_job, "Format dates", s, max_tries=1)
finally:
    wb_ai.ask = real_ask
pj = json.loads((pk_job.repo / "package.json").read_text(encoding="utf-8"))
check("an approved package stays", pj["dependencies"] == {"dayjs": "1.11.13"} and not r.tries[0].refused,
      f"{pj} {r.tries[0].refused}")

answers = ["NEEDS_APPROVAL: lodash for sums"]
prompts.clear()
wb_ai.ask = fake_ask
try:
    r = builder.build_step(job, "Use lodash", s)
finally:
    wb_ai.ask = real_ask
check("NEEDS_APPROVAL stops the step and asks", r.status == "ask" and "lodash" in r.message)

dup = runner.Result("build", "tests/T/T.csproj", "dotnet build", False, 1, 1.0,
                    "tests\\T\\ShopFactory.cs(52,29): error CS0111: Type 'ShopFactory' already defines a member called "
                    "'ConfigureWebHost' with the same parameter types [tests\\T\\T.csproj]")
hints = builder.compiler_hints(cs, [dup])
check("CS0111: a method written twice gets a hint to merge into the existing one",
      any("tests/T/ShopFactory.cs: ShopFactory now has two ConfigureWebHost methods" in h for h in hints), str(hints))
ext = runner.Result("build", "src/A/A.csproj", "dotnet build", False, 1, 1.0,
                    "src\\A\\Controllers\\AuthController.cs(59,18): error CS1061: 'DbSet<ClientProfile>' does not "
                    "contain a definition for 'FirstOrDefaultAsync' and no accessible extension method "
                    "'FirstOrDefaultAsync' accepting a first argument of type 'DbSet<ClientProfile>' could be found")
check("CS1061 on a known extension method gets its using line",
      builder.compiler_hints(cs, [ext]) == ["FirstOrDefaultAsync is an extension method from "
                                            "Microsoft.EntityFrameworkCore: add `using Microsoft.EntityFrameworkCore;` "
                                            "at the top of src/A/Controllers/AuthController.cs."],
      str(builder.compiler_hints(cs, [ext])))
(cs / "Auth").mkdir(exist_ok=True)
(cs / "Auth" / "AuthController.cs").write_bytes(b"\xef\xbb\xbfusing System;\r\nusing System.Linq;\r\n\r\nclass C { AppUser u; }\r\n")
(cs / "Auth" / "Locked.cs").write_text("class L { AppUser u; }\n", encoding="utf-8")
miss = runner.Result("build", "Shop.csproj", "dotnet build", False, 1, 1.0,
                     "Auth\\AuthController.cs(4,11): error CS0246: The type or namespace name 'AppUser' could not be found\n"
                     "Auth\\Locked.cs(1,11): error CS0246: The type or namespace name 'AppUser' could not be found")
added = builder.add_usings(cs, [miss], {"Auth/Locked.cs"})
body = (cs / "Auth" / "AuthController.cs").read_bytes()
check("the using lines the compiler asks for are added by the program (after the last using, BOM and CRLF kept), "
      "never to the client's tests, and only once",
      added == ["Auth/AuthController.cs: using Shop.Models;"]
      and body == b"\xef\xbb\xbfusing System;\r\nusing System.Linq;\r\nusing Shop.Models;\r\n\r\nclass C { AppUser u; }\r\n"
      and "Shop.Models" not in (cs / "Auth" / "Locked.cs").read_text(encoding="utf-8")
      and builder.add_usings(cs, [miss], {"Auth/Locked.cs"}) == [], f"{added} {body!r}")
files = ["src/Shop.Api/Program.cs", "tests/Shop.Tests/ShopFactory.cs", "tests/Shop.Tests/CartTests.cs", "client/src/App.jsx"]
check("the Builder always sees the files its step or the last errors name (path, file name or class name)",
      builder.must_see("Update ShopFactory to expose SeedUsers; the check runs `client/src/App.jsx`", files)
      == ["tests/Shop.Tests/ShopFactory.cs", "client/src/App.jsx"]
      and builder.must_see("tests\\Shop.Tests\\CartTests.cs(3,1): error CS1002", files) == ["tests/Shop.Tests/CartTests.cs"]
      and builder.must_see("Add a login endpoint", files) == [], str(builder.must_see("Update ShopFactory", files)))

shutil.rmtree(TMP, ignore_errors=True)
if failures:
    print("\n" + "\n".join(failures))
    sys.exit(1)
print("\nAll Builder tests passed.")
