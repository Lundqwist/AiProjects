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
from team import builder, edits, jobs  # noqa: E402
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
check("repeated blocks and blocks that change nothing are dropped and reported; DISPUTE is read",
      len(p.edits) == 1 and len(p.problems) == 2 and p.disputes == ["2 - a.js:4 already returns 0"],
      f"{len(p.edits)} {p.problems} {p.disputes}")
check("NEEDS_APPROVAL is read", edits.parse("NEEDS_APPROVAL: dayjs for date maths").needs_approval
      == ["dayjs for date maths"])

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
out = edits.apply(repo, [E("src/missing.js", "a", "b")])
check("an edit to a file that doesn't exist says how to create it", not out[0].ok and "=== FILE:" in out[0].why)

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

answers = ["NEEDS_APPROVAL: lodash for sums"]
prompts.clear()
wb_ai.ask = fake_ask
try:
    r = builder.build_step(job, "Use lodash", s)
finally:
    wb_ai.ask = real_ask
check("NEEDS_APPROVAL stops the step and asks", r.status == "ask" and "lodash" in r.message)

shutil.rmtree(TMP, ignore_errors=True)
if failures:
    print("\n" + "\n".join(failures))
    sys.exit(1)
print("\nAll Builder tests passed.")
