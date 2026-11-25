"""Tests for the Scout, the Checker and the team loop (team\\scout.py, checker.py, loop.py), with fake models.

Run: .venv\\Scripts\\python.exe app\\test_loop.py      (needs node and git; no models, no network)
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wb_ai  # noqa: E402
from team import checker, jobs, llm, loop, scout  # noqa: E402
from wb_settings import Settings  # noqa: E402

failures: list[str] = []
TMP = Path(tempfile.mkdtemp(prefix="wb-loop-test-"))


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(f"{name}: {detail}")


# ---- reading model answers ----------------------------------------------------------------------

check("first_json: inside ```json, with braces in strings",
      llm.first_json('Here:\n```json\n{"a": "x { y }", "b": [1, {"c": 2}]}\n```') == {"a": "x { y }", "b": [1, {"c": 2}]})
check("first_json: bare object after a sentence, escaped quotes",
      llm.first_json('Sure. {"a": "say \\"hi\\"", "n": 1} done') == {"a": 'say "hi"', "n": 1})
check("first_json: none", llm.first_json("no json here") is None)

files = ["src/cart.js", "src/money.js", "test/cart.test.js"]
p = scout.parse(json.dumps({
    "requirements": ["amounts rounded to 2 decimals", "FREESHIP makes shipping 0"],
    "places": [{"requirement": 2, "file": "./src/cart.js", "where": "shipping()", "change": "0 when FREESHIP"},
               {"requirement": 1, "file": "cart.js", "where": "summary()"}],
    "files": [{"path": "src/money.js", "why": "rounding helper"}, {"path": "src/nowhere.js"},
              {"path": "src/codes.js", "new": True}]}), files)
check("Scout: paths are matched to real files (./ and bare names), places add their files",
      p.files == ["src/money.js", "src/cart.js"] and p.places[0]["file"] == "src/cart.js", str(p.files))
check("Scout: an invented path is a problem, a new file is not",
      p.problems == ["the Scout named src/nowhere.js, which isn't in the repository"], str(p.problems))
check("Scout: the pack names requirements and places for the Builder",
      "R2. FREESHIP makes shipping 0" in p.for_prompt() and "R2 → src/cart.js: shipping() — 0 when FREESHIP"
      in p.for_prompt(), p.for_prompt())
check("Scout: no JSON is not a usable pack", not scout.parse("I think you should edit cart.js", files).ok)

src = TMP / "outline"
(src / "src").mkdir(parents=True)
(src / "src" / "cart.js").write_text("export const SHIPPING = 49;\n\nexport function shipping(cart) {\n  return 1;\n}\n"
                                     "export const total = (c) => 1;\n", encoding="utf-8")
(src / "Program.cs").write_text('app.MapGet("/api/todos", () => 1);\npublic class TodoStore\n{\n'
                                "    public Todo Add(string title)\n    {\n", encoding="utf-8")
m = scout.repo_map(src, ["src/cart.js", "Program.cs"])
check("the repository map lists functions, constants, endpoints and classes by line",
      all(x in m for x in ("3 export function shipping(cart) {", "1 export const SHIPPING = 49;",
                           "6 export const total", '1 app.MapGet("/api/todos"', "2 public class TodoStore",
                           "4 public Todo Add(string title)")), m)

v = checker.parse("R1: DONE - src/cart.js:40 rounds\nR2: MISSING - shipping() ignores the code\nVERDICT: PASS\n"
                  "FIX list:\n(none)", 2)
check("Checker: PASS with a MISSING requirement counts as FIX, and the list comes from it",
      v.verdict == "FIX" and v.fix_list == ["R2: shipping() ignores the code"], f"{v.verdict} {v.fix_list}")
v = checker.parse("**R1:** DONE – ok\n**R2:** WRONG – 'save10' fails\n**VERDICT:** FIX\nFIX list:\n"
                  "1. src/cart.js:12 - code not upper-cased - use code.trim().toUpperCase()\nASK: none", 2)
check("Checker: bold markdown, a FIX list, ASK none",
      v.verdict == "FIX" and v.fix_list == ["src/cart.js:12 - code not upper-cased - use code.trim().toUpperCase()"]
      and not v.question, f"{v.verdict} {v.fix_list} {v.question!r}")
v = checker.parse("R1: DONE - fine\nR2: DONE - fine", 2)
check("Checker: every requirement DONE without a verdict line is PASS", v.verdict == "PASS")
v = checker.parse("R1: DONE - fine\nVERDICT: ASK\nASK: The client asked for kr, should totals show öre?", 1)
check("Checker: ASK with its question", v.verdict == "ASK" and "öre" in v.question)
v = checker.parse("R1: DONE - fine\nR2: MISSING - no null\nVERDICT: PASS | FIX | ASK", 2)
check("Checker: the copied template line is not a PASS", v.verdict == "FIX" and v.fix_list == ["R2: no null"],
      f"{v.verdict} {v.fix_list}")
check("Checker: no verdict and requirements missing is no verdict", checker.parse("hmm", 2).verdict == "")

# ---- the loop, with fake models ------------------------------------------------------------------

client = TMP / "client"
(client / "src").mkdir(parents=True)
(client / "test").mkdir()
(client / "package.json").write_text(json.dumps({"name": "c", "version": "1.0.0", "private": True,
                                                 "scripts": {"test": "node --test"}}), encoding="utf-8")
(client / "src" / "sum.js").write_text("exports.sum = (a, b) => a - b;\n", encoding="utf-8")
(client / "test" / "sum.test.js").write_text(
    "const test = require('node:test');\nconst assert = require('node:assert');\nconst { sum } = require('../src/sum');\n"
    "test('adds', () => assert.strictEqual(sum(2, 3), 5));\n", encoding="utf-8")

SCOUT = json.dumps({"requirements": ["sum(a, b) returns a + b", "sum() with no arguments returns 0"],
                    "places": [{"requirement": 1, "file": "src/sum.js", "where": "exports.sum"},
                               {"requirement": 2, "file": "src/sum.js", "where": "exports.sum"}],
                    "files": [{"path": "src/sum.js", "why": "the function"}], "notes": "CommonJS"})
BUILDS = ["=== EDIT: src/sum.js ===\n<<<<<<< SEARCH\n(a, b) => a - b\n=======\n(a, b) => a + b\n>>>>>>> REPLACE\nRUN: test",
          "=== EDIT: src/sum.js ===\n<<<<<<< SEARCH\n(a, b) => a + b\n=======\n(a = 0, b = 0) => a + b\n>>>>>>> REPLACE\nRUN: test"]
CHECKS = ["R1: DONE - src/sum.js:1 adds\nR2: MISSING - sum() gives NaN\nVERDICT: PASS",
          "R1: DONE - src/sum.js:1 adds\nR2: DONE - defaults to 0\nVERDICT: PASS\nFIX list:\n(none)"]
calls: dict[str, list[str]] = {"scout": [], "builder": [], "checker": []}


def fake_ask(s, messages, **kw):
    system, user = messages[0]["content"], messages[-1]["content"]
    role = "scout" if "You are the Scout" in system else "checker" if "You are the Checker" in system else "builder"
    calls[role].append(user)
    text = {"scout": [SCOUT], "builder": BUILDS, "checker": CHECKS}[role][len(calls[role]) - 1]
    return wb_ai.Answer(text, role, "here")


s = Settings()
s.online_mode = "off"
real_ask = wb_ai.ask
wb_ai.ask = fake_ask
try:
    job = jobs.create(TMP / "Jobs", "Test", "Sum", str(client))
    card = job.card()
    card.goal, card.steps = "A working sum", [jobs.Step("Make sum add its arguments, and sum() return 0")]
    job.save_card(card)
    gate: list[str] = []
    out = loop.run_job(s, job, ask_fn=lambda t, text, b: (gate.append(t), b[-1])[1])
    check("the Start gate: no click, no work", out.status == "not started" and gate == ["Start this job?"]
          and not any(calls.values()) and jobs.git(job.repo, "log", "--oneline").count("\n") == 0)

    asked: list[str] = []
    out = loop.run_job(s, job, ask_fn=lambda t, text, b: (asked.append(t), b[0] if t == "Start this job?" else b[-1])[1],
                       open_review=lambda j: asked.append("opened"))
finally:
    wb_ai.ask = real_ask

st = out.steps[0] if out.steps else None
check("the job is done: Scout, Builder, Checker FIX, Builder fix round, Checker PASS",
      out.status == "done" and st and st.ok and len(st.verdicts) == 2 and len(st.builds) == 2
      and [len(calls[r]) for r in ("scout", "builder", "checker")] == [1, 2, 2], f"{out.status} {calls.keys()}")
check("the Builder got the Scout's requirements and places", "R2. sum() with no arguments returns 0" in calls["builder"][0]
      and "R1 → src/sum.js: exports.sum" in calls["builder"][0])
check("the fix round got the Checker's list", "R2: sum() gives NaN" in calls["builder"][1], calls["builder"][1][-600:])
check("the Checker got the diff, the changed file with line numbers and its own test run",
      "-exports.sum = (a, b) => a - b;" in calls["checker"][0] and "   1| exports.sum = (a, b) => a + b;"
      in calls["checker"][0] and "test · package.json · OK" in calls["checker"][0], calls["checker"][0][:1500])
check("the second Checker round sees the whole step's diff", "-exports.sum = (a, b) => a - b;" in calls["checker"][1]
      and "+exports.sum = (a = 0, b = 0) => a + b;" in calls["checker"][1])
log = jobs.git(job.repo, "log", "--format=%s")
check("a commit per Builder round", log.splitlines() == ["Builder: fix round 1 for Make sum add its arguments, and sum() return 0",
                                                          "Builder: Make sum add its arguments, and sum() return 0",
                                                          "The client's code as received"], log)
review = Path(out.review).read_text(encoding="utf-8") if out.review else ""
check("review.md: result, requirements as the Checker last saw them, commits",
      "Result: **done**" in review and "R2 DONE: sum() with no arguments returns 0" in review
      and "Builder: fix round 1" in review, review)
check("the Review gate asks before opening VS Code (and didn't, on no)",
      asked == ["Start this job?", "Review the job?"], str(asked))
check("the job card shows the step passed", job.card().steps[0].status == "pass")
check("the Scout's and Checker's answers are kept", len(list((job.folder / "scout").glob("*.md"))) == 1
      and len(list((job.folder / "checker").glob("*.md"))) == 2)

# failing checks are FIX without asking a model
(job.repo / "src" / "sum.js").write_text("exports.sum = (a, b) => a * b;\n", encoding="utf-8")
jobs.git(job.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "break")
wb_ai.ask = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model should be asked"))
try:
    v = checker.check(s, job, "x", ["r"], jobs.git(job.repo, "rev-parse", "HEAD~1"))
finally:
    wb_ai.ask = real_ask
check("failing checks are FIX by the checks themselves, with the errors for the Builder",
      v.verdict == "FIX" and v.by == "the checks" and "FAILED" in v.fix_text(), v.fix_text())

# only a test the Builder wrote fails: the model decides, and can't PASS
(job.repo / "src" / "sum.js").write_text("exports.sum = (a = 0, b = 0) => a + b;\n", encoding="utf-8")
(job.repo / "test" / "own.test.js").write_text(
    "const test = require('node:test');\nconst assert = require('node:assert');\nconst { sum } = require('../src/sum');\n"
    "test('own', () => assert.strictEqual(sum(1, 1), 3));\n", encoding="utf-8")
jobs.git(job.repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
jobs.git(job.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "own test")
seen: list[str] = []
wb_ai.ask = lambda s_, m, **k: (seen.append(m[-1]["content"]),
                                wb_ai.Answer("R1: DONE - fine\nVERDICT: PASS", "checker", "here"))[1]
try:
    v = checker.check(s, job, "x", ["sum adds"], jobs.git(job.repo, "rev-parse", "HEAD~1"))
finally:
    wb_ai.ask = real_ask
check("only the Builder's own test fails: the model is asked which is wrong, and its PASS becomes FIX",
      seen and "Tests the Builder wrote itself fail: test/own.test.js" in seen[0] and v.verdict == "FIX"
      and "test/own.test.js fails" in v.fix_list[0], f"{v.verdict} {v.fix_list}")

shutil.rmtree(TMP, ignore_errors=True)
if failures:
    print("\n" + "\n".join(failures))
    sys.exit(1)
print("\nAll loop tests passed.")
