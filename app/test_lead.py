"""Tests for the Lead (team\\lead.py) and the coding team in the conversation (wb_team_tools.py), with fake models.

Run: .venv\\Scripts\\python.exe app\\test_lead.py      (needs node and git; no models, no network)
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.modules.setdefault("wb_create", types.SimpleNamespace(make_room=lambda: []))  # no GPU work in tests
import wb_access  # noqa: E402
import wb_ai  # noqa: E402
import wb_notify  # noqa: E402
import wb_team_tools  # noqa: E402
from team import jobs, lead  # noqa: E402
from wb_settings import Settings  # noqa: E402

failures: list[str] = []
TMP = Path(tempfile.mkdtemp(prefix="wb-lead-test-"))


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(f"{name}: {detail}")


client = TMP / "client"
(client / "src").mkdir(parents=True)
(client / "test").mkdir()
(client / "package.json").write_text(json.dumps({"name": "c", "version": "1.0.0", "private": True,
                                                 "scripts": {"test": "node --test"}}), encoding="utf-8")
(client / "src" / "sum.js").write_text("exports.sum = (a, b) => a - b;\n", encoding="utf-8")
(client / "test" / "sum.test.js").write_text(
    "const test = require('node:test');\nconst assert = require('node:assert');\nconst { sum } = require('../src/sum');\n"
    "test('adds', () => assert.strictEqual(sum(2, 3), 5));\n", encoding="utf-8")
(client / "README.md").write_text("# Sum\nAdds numbers for the shop.\n", encoding="utf-8")

CARD = """Here is the card:

```markdown
# Job: Fix sum
Client: someone else
Stack pack: python
Goal: sum adds its two numbers
Acceptance checks:
- the tests pass
Out of scope: nothing else
Steps:
1. Make sum(a, b) return a + b
2. Check sum(0, 0) is 0 - check: a test for it
Questions for Bjarne:
none
```"""
seen: dict[str, list[str]] = {"lead": [], "scout": [], "builder": [], "checker": []}
replies = {
    "lead": [CARD],
    "scout": [json.dumps({"requirements": ["sum adds"], "files": [{"path": "src/sum.js"}],
                          "places": [{"requirement": 1, "file": "src/sum.js", "where": "exports.sum"}]})] * 4,
    "builder": ["=== EDIT: src/sum.js ===\n<<<<<<< SEARCH\n(a, b) => a - b\n=======\n(a, b) => a + b\n>>>>>>> REPLACE\nRUN: test",
                "=== FILE: test/zero.test.js ===\n```js\nconst test = require('node:test');\nconst assert = "
                "require('node:assert');\nconst { sum } = require('../src/sum');\n"
                "test('zero', () => assert.strictEqual(sum(0, 0), 0));\n```\nRUN: test"],
    "checker": ["R1: DONE - src/sum.js:1 adds\nVERDICT: PASS"] * 4,
}


def fake_ask(s, messages, **kw):
    system, user = messages[0]["content"], messages[-1]["content"]
    role = next(r for r, key in (("lead", "You are the Lead"), ("scout", "You are the Scout"),
                                 ("checker", "You are the Checker"), ("builder", "You are the Builder")) if key in system)
    seen[role].append(user)
    return wb_ai.Answer(replies[role].pop(0), role, "here")


s = Settings()
s.online_mode, s.jobs_dir, s.team_probes = "off", str(TMP / "Jobs"), False
wb_team_tools.configure(s)
real_ask = wb_ai.ask
wb_ai.ask = fake_ask
asked: list[str] = []
answers = {"Copy code for a new job?": "Copy it", "Start this job?": "Start"}
wb_access.configure(s, lambda title, text, buttons: (asked.append(title), answers.get(title, buttons[-1]))[1])
announced: list[tuple[str, str]] = []
wb_notify.subscribe(lambda text, path: announced.append((text, path)))
try:
    # ---- the Lead, through the conversation's tool ------------------------------------------------
    answers["Copy code for a new job?"] = "Don't copy"
    out = wb_team_tools.new_coding_job("Acme", "sum is wrong", str(client))
    check("copying the client's code asks on screen, and no means no job",
          "Not copied" in out and asked == ["Copy code for a new job?"] and not (TMP / "Jobs").exists(), out)
    answers["Copy code for a new job?"] = "Copy it"
    out = wb_team_tools.new_coding_job("Acme", "sum subtracts instead of adding; sum(0, 0) must be 0", str(client))
    job = wb_team_tools._find("")
    card = job.card() if job else None
    check("a new job: copied on its own branch, card written by the Lead", job is not None and card.title == "Fix sum"
          and jobs.git(job.repo, "branch", "--show-current").startswith("ai/") and "# Job: Fix sum" in out, out)
    check("the Lead saw the request and the repository (packs, files, README)",
          "sum(0, 0) must be 0" in seen["lead"][0] and "Stack packs found: web" in seen["lead"][0]
          and "src/sum.js" in seen["lead"][0] and "Adds numbers for the shop" in seen["lead"][0], seen["lead"][0][:800])
    check("the program holds the card to the rules: the Runner's stack pack, the client, a check per step, no 'none'",
          card.stack == "web" and card.client == "Acme" and card.steps[0].check == "the build and the tests pass"
          and card.steps[1].check == "a test for it" and card.questions == [], card.to_markdown())
    check("nothing ran yet: no Scout, Builder or Checker", not seen["scout"] and not seen["builder"])
    check("the request is kept with the job for the Scout, Builder and Checker",
          "sum(0, 0) must be 0" in job.request())

    # ---- revise -----------------------------------------------------------------------------------
    card.steps[0].status = "pass"
    job.save_card(card)
    replies["lead"].append(CARD.replace("2. Check sum(0, 0) is 0 - check: a test for it",
                                        "2. Check sum(0, 0) is 0 - check: a test for it\n3. Add a README line - check: "
                                        "README mentions zero"))
    out = wb_team_tools.update_coding_job("fix sum", "also say in the README that sum(0, 0) is 0")
    c2 = job.card()
    check("the Lead revises the card from what the user says; steps already done stay done",
          len(c2.steps) == 3 and c2.steps[0].status == "pass" and c2.steps[2].text == "Add a README line"
          and "also say in the README" in seen["lead"][1] and "1. [x] Make sum(a, b) return a + b" in seen["lead"][1],
          c2.to_markdown())
    c2.steps = c2.steps[:2]
    c2.steps[0].status = "todo"
    job.save_card(c2)

    # ---- start: in the background, Start gate on screen, announced at the end ----------------------
    asked.clear()
    out = wb_team_tools.start_coding_job("acme")
    second = wb_team_tools.start_coding_job("acme")
    t = wb_team_tools._running["thread"]
    if t:
        t.join(timeout=240)
    check("start runs in the background and only one job at a time",
          "Asked on screen" in out and "one at a time" in second,
          f"{out} | {second}")
    check("the Start gate was asked on screen", "Start this job?" in asked, str(asked))
    text = announced[-1][0] if announced else ""
    check("the end is announced in the conversation with the review file", "is done" in text and "review.md" in text
          and announced[-1][1].endswith("review.md") and "PASS: Make sum" in text, text)
    check("both steps passed and were committed", [st.status for st in job.card().steps] == ["pass", "pass"]
          and len(jobs.git(job.repo, "log", "--oneline").splitlines()) == 3, job.card().to_markdown())
    check("the review gate asked before opening VS Code (and didn't, on no)", "Review the job?" in asked, str(asked))
    status = wb_team_tools.coding_job_status("")
    check("status: the card, not running, the log and the review", "Running now: no" in status
          and "Review gate" in status and "review.md" in status, status[-600:])
    check("list shows the job with its progress", "Fix sum (2 of 2 steps passed)" in wb_team_tools.list_coding_jobs())
    check("an unknown job is said so", "No coding job matches" in wb_team_tools.start_coding_job("nothing like it"))

    # a Start that isn't clicked starts nothing
    answers["Start this job?"] = "Not now"
    c3 = job.card()
    c3.steps.append(jobs.Step("Something more"))
    job.save_card(c3)
    wb_team_tools.start_coding_job("")
    t = wb_team_tools._running["thread"]
    if t:
        t.join(timeout=60)
    check("without a click on Start nothing runs, and the conversation is told",
          "wasn't started" in announced[-1][0] and job.card().steps[2].status == "todo", announced[-1][0])
finally:
    wb_ai.ask = real_ask

# ---- reading the Lead's card ---------------------------------------------------------------------
c = jobs.JobCard.from_markdown(lead._card_text("<think>plan</think>\n# Job: X\nClient: own\nStack pack: web\nGoal: g\n"
                                               "Acceptance checks:\n- t\nOut of scope: -\nSteps:\n"
                                               + "".join(f"{i}. step {i} - check: c\n" for i in range(1, 11))
                                               + "Questions for Bjarne:\n1. Which colour?"))
notes = lead.tidy(c, ["web"], "own")
check("a card with too many steps is cut to 8, with a note; questions are kept",
      len(c.steps) == 8 and any("10 steps" in n for n in notes) and c.questions == ["Which colour?"], str(notes))
two = ("Draft:\n# Job: Draft\nClient: own\nStack pack: web\nGoal: g\nAcceptance checks:\n- t\nOut of scope: -\nSteps:\n"
       "1. draft step - check: c\nQuestions for Bjarne:\nnone\n\nFinal:\n```markdown\n# Job: Final\nClient: own\n"
       "Stack pack: web\nGoal: g\nAcceptance checks:\n- t\n- t\nOut of scope: -\nSteps:\n1. Do A - check: c\n"
       "2. do a. - check: c\n3. Do B - check: c\nQuestions for Bjarne:\nnone\n```")
c = jobs.JobCard.from_markdown(lead._card_text(two))
notes = lead.tidy(c, ["web"], "own")
check("of a draft and a card only the last card is read; repeated steps and checks are dropped",
      c.title == "Final" and [st.text for st in c.steps] == ["Do A", "Do B"] and c.checks == ["t"], c.to_markdown())

# ---- the client's test helpers a step names can be changed ------------------------------------------
from team import edits  # noqa: E402
hrepo = TMP / "helpers"
(hrepo / "tests").mkdir(parents=True)
(hrepo / "tests" / "ShopFactory.cs").write_text("public class ShopFactory : WebApplicationFactory<Program> { }\n",
                                                 encoding="utf-8")
(hrepo / "tests" / "CartTests.cs").write_text("public class CartTests { [Fact] public void Adds() { } }\n",
                                              encoding="utf-8")
locked = {"tests/ShopFactory.cs", "tests/CartTests.cs"}
check("a test helper the step names is unlocked; a file with test cases stays locked, named or not",
      edits.unlocked_helpers(hrepo, locked, "Give ShopFactory a SeedUsers property; make CartTests pass")
      == {"tests/ShopFactory.cs"}
      and edits.unlocked_helpers(hrepo, locked, "Add a login endpoint") == set())

# ---- the coverage check: every item of the request is in some step ---------------------------------
REQ = """# Phase: users

Intro text.

- Seed the users from configuration `SeedUsers`.
- An `AuthController`:
  - `POST /login` signs in;
    200 with the role.
  - `GET /me` gives the role.
- Tests: each user logs in.

Closing paragraph.
"""
items = lead.request_items(REQ)
check("request items: list lines with continuations; a heading item becomes its sub-items, named",
      items == ["Seed the users from configuration `SeedUsers`.",
                "An `AuthController` → `POST /login` signs in; 200 with the role.",
                "An `AuthController` → `GET /me` gives the role.", "Tests: each user logs in."], str(items))
check("a request with fewer than 2 items gives none to check", lead.request_items("Fix sum.\n- one thing") == [])

ONE_STEP = CARD.replace("2. Check sum(0, 0) is 0 - check: a test for it\n", "").replace("# Job: Fix sum", "# Job: Users")
FULL = ONE_STEP.replace("1. Make sum(a, b) return a + b", "1. Seed users\n2. Login and me endpoints\n3. Login tests")
cov_replies: list[str] = []
lead_seen: list[str] = []


def cov_ask(s, messages, **kw):
    lead_seen.append(messages[0]["content"][:40] + " | " + messages[-1]["content"])
    return wb_ai.Answer(cov_replies.pop(0), "lead", "here")


wb_ai.ask = cov_ask
try:
    cov_job = jobs.create(TMP / "Jobs", "Acme", "users", str(client))
    cov_replies[:] = [ONE_STEP, "I1: 1\nI2: MISSING\nI3: missing\nI4: MISSING", FULL, "I1: 1\nI2: 2\nI3: 2\nI4: 3"]
    c4, notes = lead.plan(s, cov_job, "Brief:\n- brief item\n- another\n\n" + REQ, cover=REQ)
    check("a card that drops part of the request is rewritten once, told what it left out",
          [st.text for st in c4.steps] == ["Seed users", "Login and me endpoints", "Login tests"]
          and "left out these parts" in lead_seen[2] and "`GET /me` gives the role" in lead_seen[2]
          and "brief item" not in lead_seen[1] and any("rewrote" in n for n in notes), str(notes) + str(c4.steps))
    cov_replies[:] = [ONE_STEP, "I1: 1\nI2: 1\nI3: MISSING\nI4: MISSING", ONE_STEP, "I1: 1\nI2: 1\nI3: MISSING\nI4: 1"]
    c5, notes = lead.plan(s, cov_job, REQ)
    check("items still missing after the rewrite become steps of their own",
          len(c5.steps) == 2 and c5.steps[1].text == "An `AuthController` → `GET /me` gives the role."
          and any("added as steps" in n for n in notes), str(notes) + str([st.text for st in c5.steps]))
    cov_replies[:] = [ONE_STEP, "I can't tell.", "Still can't."]
    c6, notes = lead.plan(s, cov_job, REQ)
    check("an unreadable coverage answer is asked once more without thinking, then changes nothing",
          len(c6.steps) == 1 and not cov_replies, str(notes))
    check("the coverage answer is written to the job log", "Lead · coverage check" in
          (cov_job.folder / "job-log.md").read_text(encoding="utf-8"))
    req_file = REQ.replace("- Tests: each user logs in.", "- Tests in `tests/LoginTests.cs`: each user logs in.")
    cov_replies[:] = [FULL, "I1: 1\nI2: 2\nI3: 2\nI4: 3", FULL, "I1: 1\nI2: 2\nI3: 2\nI4: 3"]
    c7, notes = lead.plan(s, cov_job, req_file)
    check("an item naming a file no step names is missing even when the model says it's covered",
          c7.steps[-1].text.endswith("`tests/LoginTests.cs`: each user logs in.") and len(c7.steps) == 4,
          str([st.text for st in c7.steps]))
finally:
    wb_ai.ask = real_ask

steps = [jobs.Step("Add the `POST /api/orders` endpoint"), jobs.Step("Write tests for `GET /api/admin/orders`")]
check("an API route the request names must be in a step that builds, not only in a test step; test items exempt",
      lead.unnamed_files("Admin: `GET /api/admin/orders?status=Open` lists orders", steps)
      == ["/api/admin/orders?status=Open"]
      and lead.unnamed_files("`POST /api/orders` places an order", steps) == []
      and lead.unnamed_files("Tests: `GET /api/admin/orders` gives 403 to others", steps) == [])

# ---- the chain reads phase requests as main has them ----------------------------------------------
from team import chain  # noqa: E402
proj = TMP / "proj"
(proj / "phases").mkdir(parents=True)
jobs.git(proj, "init", "-q", "-b", "main")
(proj / "phases" / "1-a.md").write_text("old request\n", encoding="utf-8")
jobs.git(proj, "add", "-A")
jobs.git(proj, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "old")
jobs.git(proj, "branch", "ai/phase-1")
(proj / "phases" / "1-a.md").write_text("clarified request\n", encoding="utf-8")
jobs.git(proj, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", "clarified")
jobs.git(proj, "switch", "-q", "ai/phase-1")
check("a phase request is read from main even when the folder stands on an older phase branch",
      chain.read_request(proj.resolve(), proj / "phases" / "1-a.md") == "clarified request")
outside = TMP / "outside.md"
outside.write_text("from disk\n", encoding="utf-8")
check("a request outside the repository is read from disk", chain.read_request(proj.resolve(), outside) == "from disk")

shutil.rmtree(TMP, ignore_errors=True)
if failures:
    print("\n" + "\n".join(failures))
    sys.exit(1)
print("\nAll Lead tests passed.")
