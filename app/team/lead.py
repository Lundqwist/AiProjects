"""The Lead: turns your request into a job card (goal, checks, steps, questions). It never writes code.

It gets your words and a short look at the job's repository (the stack packs found, the projects, the file list and
the README), and writes the card in the blueprint's form, which jobs.JobCard reads back. The program then holds it
to the rules it can check: the stack pack is the one the Runner found, at most 8 steps, and every step has a check.
`revise` changes a card from what you say next (answers to its questions, changes of plan).
"""
from __future__ import annotations

import re
from pathlib import Path

from team import jobs, llm, runner
from wb_settings import Settings

PROMPT = (Path(__file__).resolve().parent / "prompts" / "lead.md").read_text(encoding="utf-8")
MAX_STEPS = 8
FILES_SHOWN = 80
README_CHARS = 1500


def repo_brief(repo: Path) -> str:
    """What the Lead is told about the code: packs, projects, files, the start of the README."""
    from team import builder   # (builder doesn't import this module)
    packs = runner.detect(repo)
    projects = runner.find_projects(repo)
    files = builder.repo_files(repo)
    readme = next((repo / n for n in ("README.md", "readme.md", "README.txt", "README") if (repo / n).exists()), None)
    out = [f"Stack packs found: {', '.join(packs) or 'none'}",
           "Projects: " + (", ".join(f"{p.kind} {p.rel(repo)}" for p in projects) or "none"),
           f"Files ({len(files)}):\n" + "\n".join(files[:FILES_SHOWN]) + ("\n…" if len(files) > FILES_SHOWN else "")]
    if readme:
        out.append(f"README (start):\n{readme.read_text(encoding='utf-8', errors='replace')[:README_CHARS]}")
    return "\n\n".join(out)


def _card_text(reply: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", reply, flags=re.S)
    m = re.search(r"```(?:markdown|md)?\s*\n(.*?# Job:.*?)\n```", text, re.S)
    if m:
        return m.group(1)
    i = text.find("# Job:")
    return text[i:] if i >= 0 else ""


def choosable(repo: Path) -> list[str]:
    """The stack packs the Lead may choose: the ones found, or every pack for a repository with no projects yet."""
    return runner.detect(repo) or sorted(runner.load_packs())


def tidy(card: jobs.JobCard, packs: list[str], client: str) -> list[str]:
    """Hold the card to what the program can check. Returns notes on what was changed."""
    notes = []
    if packs and card.stack not in packs:
        notes.append(f"stack pack set to {packs[0]} (the Lead wrote '{card.stack}', the Runner found {', '.join(packs)})")
        card.stack = packs[0]
    if client and card.client != client:
        card.client = client
    if len(card.steps) > MAX_STEPS:
        notes.append(f"the Lead wrote {len(card.steps)} steps; kept the first {MAX_STEPS} (ask it to split the job)")
        card.steps = card.steps[:MAX_STEPS]
    for st in card.steps:
        if not st.check:
            st.check = "the build and the tests pass"
        st.status = st.status if st.status in ("pass",) else "todo"
    card.questions = [q for q in card.questions if q.strip().lower().rstrip(".") not in ("none", "no questions")]
    return notes


def _ask(s: Settings, user: str, think: bool = True) -> str:
    """The Lead's answer; "" when the model fails (Ollama aborts a model that repeats itself, Day 7). With a long
    brief a thinking model can think past its budget and answer nothing: the second try answers without thinking."""
    try:
        return llm.ask(s, "lead", PROMPT, user, max_tokens=16000 if think else 6000, temperature=0.2, think=think).text
    except Exception as e:  # noqa: BLE001 - a failed plan falls back to one step; it must not stop the job
        print(f"lead: the model failed: {type(e).__name__}: {e}", flush=True)
        return ""


ITEM = re.compile(r"^(\s*)(?:[-*]|\d+[.)])\s+(.*)$")
COVERAGE_PROMPT = """You check a job card against the request it was written from. You never write code.
For each numbered item of the request, say which step numbers of the card build ALL of it (its code, its
configuration, its tests). An item only partly built (the configuration but not the code that reads it, the code but
not its tests) is MISSING. Answer one line per item, nothing else:
I1: 2
I2: 1, 3
I3: MISSING"""


def request_items(text: str) -> list[str]:
    """The list items of a request ("- ..." / "1. ..."), each with its continuation lines and its sub-items' parent
    named, so "Tests: ..." under a heading stays one item. A request with fewer than 2 items gives none (a short
    request has nothing a card can drop unseen)."""
    items: list[dict] = []   # {"depth", "lines", "parent": index or None}
    cur = None
    for line in text.splitlines():
        m = ITEM.match(line)
        if m:
            depth = len(m.group(1).expandtabs(4))
            parent = next((k for k in range(len(items) - 1, -1, -1) if items[k]["depth"] < depth), None) \
                if cur is not None else None
            items.append({"depth": depth, "lines": [m.group(2).strip()], "parent": parent, "children": 0})
            if parent is not None:
                items[parent]["children"] += 1
            cur = len(items) - 1
        elif cur is not None and line.strip() and line[:1].isspace():
            items[cur]["lines"].append(line.strip())
        elif line.strip():
            cur = None   # a paragraph ends the list; the next list starts fresh
    out = []
    for it in items:
        if it["children"]:
            continue   # an item that introduces sub-items ("An `AuthController` at `api/auth`:") is covered by them
        body = " ".join(it["lines"])
        if it["parent"] is not None:
            body = " ".join(items[it["parent"]]["lines"]).rstrip(":")[:80] + " → " + body
        out.append(body)
    return out if len(out) >= 2 else []


def missing_items(s: Settings, items: list[str], card: jobs.JobCard) -> list[str]:
    """The request items no step of the card builds whole, by the Lead's model asked on its own (no thinking).
    An answer that can't be read counts nothing as missing: the check may only add steps, never block a job."""
    if not items:
        return []
    user = ("The request's items:\n" + "\n".join(f"I{i}. {t}" for i, t in enumerate(items, 1))
            + "\n\nThe job card's steps:\n" + "\n".join(f"{i}. {st.text}" for i, st in enumerate(card.steps, 1)))
    try:
        reply = llm.ask(s, "lead", COVERAGE_PROMPT, user, max_tokens=1500, temperature=0.0, think=False).text
    except Exception as e:  # noqa: BLE001
        print(f"lead: the coverage check failed: {type(e).__name__}: {e}", flush=True)
        return []
    reply = re.sub(r"<think>.*?</think>", "", reply, flags=re.S)
    out = []
    for m in re.finditer(r"^\s*I(\d+)\s*[:.]\s*(.*)$", reply, re.M):
        n = int(m.group(1))
        if 1 <= n <= len(items) and "missing" in m.group(2).lower() and items[n - 1] not in out:
            out.append(items[n - 1])
    return out


def plan(s: Settings, job: jobs.Job, request: str, cover: str | None = None) -> tuple[jobs.JobCard, list[str]]:
    """A job card for `request`, saved in the job folder. Returns (card, notes about what the program changed).
    `cover`: the part of the request every list item of which must be in some step (default: all of it). Day 7: a
    phase with configuration, seeding code, factory settings and tests got one step, the configuration, and the
    Checker passed it, since it holds work to the step. So the card is checked item by item; the Lead rewrites it
    once with what it left out, and items still missing become steps of their own."""
    repo = job.repo
    old = job.card()
    # the request itself is kept for the team: a step is the Lead's summary and can lose details (Day 7: "all three
    # roles" lost their names, and the Scout guessed Admin, Manager and User)
    (job.folder / "request.md").write_text(request.strip() + "\n", encoding="utf-8")
    packs = choosable(repo)
    empty = not runner.find_projects(repo)
    user = (f"Bjarne's request (client: {old.client}):\n{request}\n\n"
            f"Stack packs you can choose: {', '.join(packs)}\n\nThe repository:\n{repo_brief(repo)}"
            + ("\n\nThe repository has no projects yet. So the FIRST step creates them so they build and test: the "
               "project files (for .NET the .csproj files, for a web front end package.json with a build script), the "
               "program's entry point, and one first test that passes. The FOLLOWING steps on this same card then add "
               "every feature the request asks for. Nothing the request asks for may be out of scope." if empty else ""))
    card, notes = None, []
    for attempt in (1, 2):
        text = _card_text(_ask(s, user if attempt == 1 else user + "\n\nWrite the job card in exactly the asked form, "
                                                                    "starting with '# Job:'.", think=attempt == 1))
        if text:
            card = jobs.JobCard.from_markdown(text)
            if card.title and card.steps:
                break
    if not card or not card.steps:
        card = jobs.JobCard(title=old.title, client=old.client, stack=old.stack, goal=request.splitlines()[0][:200],
                            checks=["the build and the tests pass"],
                            steps=[jobs.Step(request.strip(), "the build and the tests pass")])
        notes.append("the Lead's card couldn't be read twice, so the whole request is one step")
    else:
        items = request_items(request if cover is None else cover)
        missing = missing_items(s, items, card)
        if missing:
            text = _card_text(_ask(s, user + "\n\nYour first card left out these parts of the request (no step builds "
                                   "them whole: code, configuration and tests):\n" + "\n".join(f"- {m}" for m in missing)
                                   + "\n\nWrite the whole job card again, starting with '# Job:', so that every part "
                                     "of the request is in a step.", think=False))
            again = jobs.JobCard.from_markdown(text) if text else None
            if again and again.title and again.steps:
                still = missing_items(s, items, again)
                if len(still) <= len(missing):
                    card, missing = again, still
                    notes.append("the Lead rewrote its card: the first one left out part of the request")
            room = MAX_STEPS - min(len(card.steps), MAX_STEPS)
            if missing and room >= 1:
                extra = missing if len(missing) <= room else missing[:room - 1] + [" ".join(missing[room - 1:])]
                card.steps += [jobs.Step(m, "the build and the tests pass") for m in extra]
            elif missing:
                card.steps[MAX_STEPS - 1].text += " Also: " + " ".join(missing)
            if missing:
                notes.append(f"{len(missing)} part(s) of the request no step built were added as steps of their own")
    notes += tidy(card, packs, old.client)
    job.save_card(card)
    job.log("Lead · job card", card.to_markdown() + ("\n" + "\n".join(notes) if notes else ""))
    return card, notes


def revise(s: Settings, job: jobs.Job, message: str) -> tuple[jobs.JobCard, list[str]]:
    """The card changed from what Bjarne says: answers to its questions, or changes of plan."""
    old = job.card()
    done = [st for st in old.steps if st.status == "pass"]
    user = (f"The job card so far:\n\n{old.to_markdown()}\n\nBjarne says:\n{message}\n\n"
            "Write the whole job card again with his answers and changes worked in. Questions he answered are gone."
            + (f" Keep the steps already done ({', '.join(st.text.splitlines()[0][:40] for st in done)}) first, "
               "marked [x]." if done else "")
            + f"\n\nStack packs you can choose: {', '.join(choosable(job.repo))}")
    text = _card_text(_ask(s, user))
    card = jobs.JobCard.from_markdown(text) if text else None
    if not card or not card.steps:
        return old, ["the Lead's new card couldn't be read; the old one is kept"]
    notes = tidy(card, choosable(job.repo), old.client)
    for st in card.steps:   # steps that were done stay done
        if any(st.text == d.text for d in done):
            st.status = "pass"
    job.save_card(card)
    job.log("Lead · job card changed", f"Bjarne: {message}\n\n" + card.to_markdown())
    return card, notes
