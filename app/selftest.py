"""End-to-end self-test: drives the real tabs with test data in a temporary folder.

Run: .venv\\Scripts\\python.exe app\\selftest.py [--skip-research]
Needs Ollama with the default models. Uses its own settings file, so your settings are untouched.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="workbench-test-"))
os.environ["AI_WORKBENCH_SETTINGS"] = str(TMP / "settings.json")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
sys.path[:0] = [str(APP_DIR)] + [str(ROOT / d) for d in ("01-rag", "02-voice", "03-invoices", "04-research")]

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from wb_settings import Settings  # noqa: E402

app = QApplication(sys.argv)
results: list[tuple[str, bool, str]] = []
dialogs: list[str] = []


def _no_dialog(answer):
    # A modal dialog would block the test forever: record it instead
    def show(parent, title, text, *a, **k):
        dialogs.append(f"{title}: {text}")
        print(f"DIALOG  {title}: {text}", flush=True)
        return answer
    return show


QMessageBox.warning = _no_dialog(QMessageBox.Ok)
QMessageBox.information = _no_dialog(QMessageBox.Ok)
QMessageBox.question = _no_dialog(QMessageBox.Yes)


def wait(cond, timeout=300):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.05)
    return False


def record(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


s = Settings()
s.rag_db, s.invoice_inbox = str(TMP / "db"), str(TMP / "inbox")
s.invoice_output, s.reports_dir = str(TMP / "output"), str(TMP / "reports")
s.brain_dir = str(TMP / "Brain")  # never the real brain
s.speak_replies = False
s.voice_mode = "hold"  # the microphone stays off until the wake word test feeds audio itself
s.ensure_folders()
s.apply()
import wb_access  # noqa: E402

wb_access.configure(s)  # default answer to any permission question: no

# Read-only PC tools: facts, folders, and never hidden or private places
import voice as _voice  # noqa: E402
import tab_chat as _chat  # noqa: E402
import wb_abilities  # noqa: E402

_info = json.loads(wb_access.pc_info())
record("access: pc_info", bool(_info.get("pc_name")) and bool(_info.get("drives")), _info.get("ip_address_on_home_network", ""))
_listed = wb_access.list_folder("downloads")
record("access: list_folder downloads", _listed.startswith("{") and "Downloads" in _listed, _listed[:80])
record("access: no hidden or private places",
       all("hidden or private" in wb_access.list_folder(p) or "doesn't exist" in wb_access.list_folder(p)
           for p in ("~/.ssh", "~/AppData", "~/.claude-hub")) and "outside" in wb_access.read_text_file(r"C:\Windows\win.ini"))
record("voice: refusal guard",
       bool(_voice.REFUSAL.search("I am an AI and do not have the capability to directly interact with your computer"))
       and bool(_voice.REFUSAL.search("Let me check your folders.")) and not _voice.REFUSAL.search("The capital is Oslo.")
       and bool(_voice.RIGHTLY_REFUSED.search("Delete everything in Downloads")))
record("chat: an expert that pretends to run things is caught",
       bool(_chat.FAKED.search("```tool_code\nipconfig\n```")) and not _chat.FAKED.search("The capital is Oslo."))
_on = wb_abilities.groups_for(list(wb_abilities.ABILITIES))
_routes = {q: wb_abilities.pick(q, _on).groups for q in (
    "Put CNN on the TV", "Sätt på CNN på tv:n", "What's on my to-do list?", "Make a song about coffee",
    "What is the capital of Norway?", "What does my handbook say about vacation days?", "Create a website for my cat")}
record("router: each message gets only the tool groups it needs",
       _routes["Put CNN on the TV"] == ["expert", "tv"] and _routes["Sätt på CNN på tv:n"] == ["expert", "tv"]
       and _routes["What's on my to-do list?"] == ["expert", "tasks"]
       and _routes["Make a song about coffee"] == ["expert", "songs"]
       and _routes["What is the capital of Norway?"] == ["expert"]
       and _routes["What does my handbook say about vacation days?"] == ["expert", "documents"]
       and _routes["Create a website for my cat"] == ["expert", "code"], str(_routes))
record("router: a follow-up keeps the last turn's tools, switched-off abilities are never offered",
       wb_abilities.pick("Make it longer", _on, ["songs"]).groups == ["expert", "songs"]
       and "tv" not in wb_abilities.pick("Put CNN on the TV", [g for g in _on if g != "tv"]).groups)
_hist = [{"role": "system", "content": "x" * 5000}] + [
    {"role": r, "content": f"{r} {i} " + "y" * 2000} for i in range(10) for r in ("user", "assistant")] + [
    {"role": "tool", "content": "z" * 9000}]
_ctx = _chat.compact_context(_hist)
record("chat: experts get a short summary of the conversation, not all of it",
       len(_ctx) <= _chat.CONTEXT_TURNS and _ctx[0]["role"] == "user" and all(m["role"] != "tool" for m in _ctx)
       and sum(len(m["content"]) for m in _ctx) < 4000,
       f"{len(_ctx)} messages, {sum(len(m['content']) for m in _ctx)} of {sum(len(m['content']) for m in _hist)} chars")
status_log = []
status = status_log.append

# 1. Settings: save from the tab and load back
from tab_settings import SettingsTab  # noqa: E402

tab = SettingsTab(s)
tab.speed.setValue(125)
tab.save()
loaded = Settings.load()
record("settings save/load", abs(loaded.speech_speed - 1.25) < 1e-6 and loaded.rag_db == s.rag_db,
       f"speed={loaded.speech_speed}")
wait(lambda: tab.models_status.text() != "", 15)
record("settings: model list from Ollama", "models installed" in tab.models_status.text(), tab.models_status.text())
tab.spotify_device.setText("Test speaker")
tab.save()
record("settings: Spotify fields save/load", Settings.load().spotify_device == "Test speaker")

# Spotify tools with a fake Spotify account (no network, your real sign-in is untouched)
import wb_spotify  # noqa: E402
import wb_spotify_tools  # noqa: E402


class FakeSpotify:
    played = []

    def search(self, q, kind="playlist", limit=5):
        return [{"name": f"{q.title()} Hits", "uri": f"spotify:{kind}:x"}]

    def devices(self):
        return [{"id": "d1", "name": "Other"}, {"id": "d2", "name": "Test speaker"}]

    def play(self, device_id, context_uri=None, uris=None):
        FakeSpotify.played.append((device_id, context_uri, uris))


real_spotify = wb_spotify.Spotify
wb_spotify.Spotify = FakeSpotify
wb_spotify_tools.configure(s)
s.spotify_ask_first = True
reply = wb_spotify_tools.spotify_play("country")
record("spotify: asks first, and a 'no' plays nothing", "did not allow" in reply and not FakeSpotify.played, reply)
s.spotify_ask_first = False
reply = wb_spotify_tools.spotify_play("country")
record("spotify: plays a found playlist on the preferred device",
       FakeSpotify.played == [("d2", "spotify:playlist:x", None)], reply)
wb_spotify.Spotify = real_spotify
s.spotify_ask_first = True
record("settings: sound devices listed", tab.input_device.count() > 1 and tab.output_device.count() > 1,
       f"{tab.input_device.count() - 1} in, {tab.output_device.count() - 1} out")

# 2. Documents: ingest the handbook, ask a question
from tab_documents import DocumentsTab  # noqa: E402

docs = DocumentsTab(s, status)
docs.ingest(sorted((ROOT / "05-eval" / "fixtures" / "handbook").glob("*.md")))
ok = wait(lambda: docs.sources.count() == 2, 120)
record("documents: add files", ok, f"{docs.sources.count()} sources")
docs.question.setText("How many vacation days do employees get?")
docs.ask()
wait(lambda: not docs.busy, 180)
answer = docs.chat[-1]["a"] if docs.chat else ""
record("documents: cited answer", "30" in answer and "[" in answer, answer[:80].replace("\n", " "))
credit = docs.chat[-1].get("credit", "") if docs.chat else ""
record("documents: says who answered and that nothing went online",
       "on this PC" in credit and "nothing was sent online" in credit, credit.replace("\n", " ")[:120])

# Online AI providers: the list saves with its limits
tab._add_provider({"name": "Test AI", "kind": "openai", "base_url": "https://x.test/v1", "model": "m",
                   "key_env": "NO_SUCH_KEY_VAR", "monthly_tokens": 5000, "enabled": True})
tab.save()
saved = Settings.load().online_providers
record("settings: online AI list saves with its monthly limit",
       any(p["name"] == "Test AI" and p["monthly_tokens"] == 5000 for p in saved), str([p["name"] for p in saved]))
import wb_ai  # noqa: E402
record("online AI without a key is skipped and says why",
       "no key" in wb_ai.provider_state(saved[-1])[1], wb_ai.provider_state(saved[-1])[1])

# Brain: a note is filed into the wiki with its source, a newer note outdates an old fact, search
# finds it, repeated corrections become a rule
import wb_brain  # noqa: E402
from tab_brain import BrainTab  # noqa: E402

wb_brain.configure(s)
bt = BrainTab(s, status)
bt.note.setText("My dog is called Pixel and she loves swimming in the lake.")
bt.add()
wait(lambda: not bt.busy, 300)


def wiki_text():
    return "\n".join(p.read_text(encoding="utf-8") for p in (wb_brain.root() / "wiki").glob("*.md"))


record("brain: note filed into the wiki with its source",
       "Pixel" in wiki_text() and "(source: [[" in wiki_text(), wiki_text().replace("\n", " | ")[:160])
wb_brain.remember("Pixel doesn't like swimming any more: she has been scared of the lake since the storm.")
record("brain: a newer note marks the old fact outdated, keeping both sources",
       "~~" in wiki_text() and "outdated by [[" in wiki_text(), wiki_text().replace("\n", " | ")[:200])
hits = wb_brain.search("What is the dog called?")
record("brain: search finds it", any("Pixel" in h.text for h in hits), hits[0].label if hits else "no hits")
for said in ("Don't read out web addresses.", "No, stop spelling out the link.", "Again: never read the web address aloud."):
    wb_brain.note_correction(said, "You can find it at w w w dot example dot com slash jobs.")
new = wb_brain.learn_rules()
record("brain: repeated corrections become a rule", bool(new) and bool(wb_brain.rules()), str(new))
bt.refresh()
record("brain tab: lists rules, pages and notes", bt.tree.topLevelItemCount() == 5 and
       bt.tree.topLevelItem(2).childCount() == 2, bt.stats.text()[:120])
# Brain map: the same brain as a graph, with where each fact came from, following the folder live
bm = bt.show_map()
g = bm.graph
kinds = {k: sum(n.kind == k for n in g.nodes.values()) for k in ("note", "page", "rule")}
record("brain map: notes, pages and rules, linked by where facts came from",
       kinds["note"] == 2 and kinds["page"] >= 1 and kinds["rule"] >= 1 and
       any(e.kind == "filed" for e in g.edges) and any(e.kind == "outdated" for e in g.edges), str(kinds))
# (notes are timed to the minute, so both test notes may share the first moment: compare with just before it)
from datetime import timedelta  # noqa: E402

before = g.counts_at(g.start - timedelta(minutes=1))
bm.slider.setValue(1000)
record("brain map: the timeline is empty before the first memory and full now",
       sum(before.values()) == 0 and g.counts_at(g.end)["notes"] == 2 and bm.canvas.visible.sum() == len(g.nodes),
       f"{before} → {g.counts_at(g.end)}")
wb_brain.add_note("The map should show this note at once.", "typed")
bm.reload_if_changed()
record("brain map: follows the folder live", bm.graph.counts_at(bm.graph.end)["notes"] == 3 and
       any(i.startswith("note:") for i in bm.canvas.fresh), bm.summary.text())
bm.grab()
bm.close()

# Create: a picture, a tune, a sung song, a story and a movie, each with a record of who made it
s.creations_dir, s.code_projects_dir = str(TMP / "Creations"), str(TMP / "Code projects")
s.code_roots = [s.code_projects_dir]
s.ensure_folders()
import wb_create  # noqa: E402
import wb_song  # noqa: E402
from tab_create import CreateTab  # noqa: E402

ct = CreateTab(s, status)
jobs = [(ct.image, "a red apple on a wooden table"), (ct.music, "happy ukulele tune"),
        (ct.song, "a short happy song about coffee"), (ct.story, "a cat who learns to swim"),
        (ct.movie, "a paper boat that sails to the sea")]
if not wb_song.installed()[0]:
    record("create: song (skipped)", True, wb_song.installed()[1])
    jobs = [j for j in jobs if j[0] is not ct.song]
for page, idea in jobs:
    if page is ct.music:
        page.seconds.setValue(4)
    if page is ct.story:
        page.length.setCurrentIndex(0)
    page.idea.setPlainText(idea)
    page.create()
    if page is ct.movie:  # the screenplay is shown first; change it, then film it without a narrator
        wait(lambda: not page.busy, 600)
        text = page.play_text.toPlainText()
        record("create: movie screenplay shown to check first",
               not page.play_box.isHidden() and "Scene 1" in text and "Picture:" in text, text[:80].replace("\n", " | "))
        page.play_text.setPlainText(re.sub(r"(?m)^Title: .*$", "Title: The Paper Boat Test", text))
        page.narration.setChecked(False)
        page.film()
    ok = wait(lambda: not page.busy, 1800 if page is ct.movie else 600)  # moving scenes take 1-2 min each
    made = page.gallery.count() == 1 and page.path is not None and page.path.exists()
    record(f"create: {page.kind} made, listed and credited", ok and made and "Made by" in page.credit.text(),
           page.credit.text().split("<br>")[0][:120] or page.state.text()[:120])
    if page is ct.movie and made:
        rec = wb_create.load_record(page.path)
        d = rec.details if rec else {}
        record("create: movie filmed from the edited screenplay, without a narrator",
               d.get("title") == "The Paper Boat Test" and str(d.get("voice", "")).startswith("none")
               and "edited by you" in " ".join(rec.notes), f"{d.get('title')} · voice {d.get('voice')}")
    if page is ct.movie and made and ct.movie.moving.isChecked():
        rec = wb_create.load_record(page.path)
        clips = list(page.path.with_name(rec.details["folder"]).glob("scene-*.mp4")) if rec else []
        record("create: movie scenes animated (LTX-Video)", bool(rec and rec.details.get("moving") and clips),
               f"{len(clips)} scene clips")
    if page is ct.song and made:
        rec = wb_create.load_record(page.path)
        d = rec.details if rec else {}
        need = wb_song.seconds_needed(d.get("lyrics", ""), wb_song.bpm_of(d.get("style", "")))
        record("create: song long enough for its lyrics", d.get("seconds", 0) >= min(need, wb_song.MAX_SECONDS),
               f"{d.get('seconds')} s for {need} s of lyrics")
ct.shutdown()  # also stops the song server the song started
time.sleep(3)
record("create: song server stopped afterwards", not wb_song._healthy(), "")

# Code: a new project is proposed, saved and opened; a change shows a diff and keeps a backup
import wb_code  # noqa: E402
from tab_code import CodeTab  # noqa: E402

opened = []
wb_code.open_in_vscode = lambda path, file=None: opened.append(str(path))
cd = CodeTab(s, status)
cd.create_project("hello-cli", "A Python script hello.py that prints a greeting for the name given as argument.")
wait(lambda: not cd.busy, 300)
names = [c.path for c in cd.changes]
record("code: new project proposed as files", any(n.endswith(".py") for n in names),
       str(names) if names else "no files; the answer was: " + cd.result.toPlainText()[:300].replace("\n", " | "))
cd.apply_changes()
proj = Path(s.code_projects_dir) / "hello-cli"
record("code: saved and opened in VS Code", any(proj.glob("*.py")) and opened == [str(proj)], str(opened))
cd.request.setPlainText("Add a --shout option that prints the greeting in capital letters.")
cd.make_change()
wait(lambda: not cd.busy, 300)
changed = [c for c in cd.changes if not c.is_new]
record("code: change shown as a diff before saving", bool(changed) and "+" in changed[0].diff(),
       str([c.path for c in cd.changes]))
cd.apply_changes()
record("code: replaced files are backed up", any((proj / ".workbench-history").rglob("*.py")))

# 3. Invoices: copy samples to the inbox and process them
import make_samples  # noqa: E402

from tab_invoices import InvoicesTab  # noqa: E402

samples = TMP / "samples"
samples.mkdir()
make_samples.INBOX = samples
for sample in make_samples.SAMPLES[:2] + make_samples.SAMPLES[-1:]:
    make_samples.make(*sample)
inv = InvoicesTab(s, status)
inv.copy_to_inbox(sorted(samples.glob("*.pdf")))
ok = wait(lambda: inv.table.rowCount() == 2 and inv.review.count() == 1 and not inv.queued, 240)
record("invoices: 2 to CSV, 1 to review", ok, f"{inv.table.rowCount()} rows, {inv.review.count()} review")
if inv.review.count():
    inv.review.setCurrentRow(0)
    record("invoices: review reason shown", "total" in inv.review_text.toPlainText(),
           inv.review_text.toPlainText().splitlines()[0] if inv.review_text.toPlainText() else "")

# 4. The conversation: spoken and typed turns in one conversation (no speakers needed)
import voice  # noqa: E402

from tab_chat import ChatTab  # noqa: E402

wav = ROOT / "02-voice" / "test-audio" / "q-sv.wav"
vt = ChatTab(s, status)
if wav.exists():
    vt.run_turn(audio=voice.load_wav(str(wav)))
    ok = wait(lambda: len(vt.turns) >= 2, 180)
    said = vt.turns[0][1] if vt.turns else ""
    reply = vt.turns[1][1] if len(vt.turns) > 1 else ""
    record("voice: speech -> answer (Swedish)", ok and "Oslo" in reply, f"'{said}' -> '{reply}'")
else:
    record("voice: speech -> answer", False, "run 02-voice/make_test_audio.py first")
vt.run_turn(text="Say hello in five words or fewer.")
ok = wait(lambda: len(vt.turns) >= 4, 120)
reply = vt.turns[-1][1] if ok else ""
record("voice: typed English after Swedish gets English", ok and "hej" not in reply.lower(), reply)
# Typed questions that need no tools go straight to the expert that fits, and the conversation goes on
vt.run_turn(text="Write a Python function that returns the square of a number. Code only.")
ok = wait(lambda: len(vt.turns) >= 6 and not vt.is_busy, 300)
reply, info = (vt.turns[-1][1], vt.turns[-1][2]) if ok else ("", "")
record("chat: a typed code question goes straight to the code expert, with who answered",
       ok and "Code expert" in info and "def " in reply and "tokens in" in info, info[:140])
vt.run_turn(text="Now make it return the cube instead.")
ok = wait(lambda: len(vt.turns) >= 8 and not vt.is_busy, 300)
cube = vt.turns[-1][1].replace(" ", "") if ok else ""
record("chat: the follow-up to an expert answer keeps the context",
       ok and any(c in cube for c in ("**3", "x*x*x", "pow(")) and "def" in cube, vt.turns[-1][1][:120].replace("\n", " "))
# A typed question about the user's documents uses the Documents ability's tool
vt.run_turn(text="What does my handbook say about how many vacation days employees get?")
ok = wait(lambda: len(vt.turns) >= 10 and not vt.is_busy, 300)
used = [n for n, _ in getattr(vt.bot, "last_steps", [])]
record("chat: a question about your documents is answered from them", ok and "ask_my_documents" in used
       and "30" in vt.turns[-1][1], f"tools {used} · {vt.turns[-1][1][:80]}")
info = vt.turns[-1][2]
record("chat: a turn is offered only some of the tools, and says so", "tools offered" in info and
       int(re.search(r"(\d+) of (\d+) tools", info).group(1)) < int(re.search(r"(\d+) of (\d+) tools", info).group(2)),
       info[-80:])
# A background job (a song, a movie, a report) tells the conversation when it is done
import wb_notify  # noqa: E402

wb_notify.announce("The song “Test” is ready and open.")
ok = wait(lambda: vt.turns[-1][1] == "The song “Test” is ready and open.", 5)
record("chat: finished background jobs appear in the conversation", ok and
       any(m.get("content") == "The song “Test” is ready and open." for m in vt.bot.history if isinstance(m, dict)))
audio, rate = vt.bot.synth("Testing the voice.", "en")
record("voice: speech synthesis", len(audio) > rate * 0.5, f"{len(audio) / rate:.1f}s at {rate} Hz, speed 1.25")

# Busy bar: shows the steps while working, hides when done
steps = []
real_set_step = vt._set_step
vt._set_step = lambda text: (steps.append(text), real_set_step(text))
vt.run_turn(text="What is two plus two?")
shown = wait(lambda: vt.is_busy, 5)
ok = wait(lambda: len(vt.turns) >= 13 and not vt.is_busy, 120)
record("voice: busy bar shows steps and hides when done", shown and ok and "Thinking…" in steps,
       f"steps {list(dict.fromkeys(steps))}, state '{vt.state.text()}'")

# Brain in the conversation: recalls what it was told, and remembers new things
vt.run_turn(text="What is my dog called?")
ok = wait(lambda: len(vt.turns) >= 15 and not vt.is_busy, 180)
used = [n for n, _ in getattr(vt.bot, "last_steps", [])]
record("voice: recalls from the brain", ok and "pixel" in vt.turns[-1][1].lower() and "recall" in used,
       f"{vt.turns[-1][1][:80]} · tools {used}")
vt.run_turn(text="Please remember that my sister Anna's birthday is on the third of March.")
ok = wait(lambda: len(vt.turns) >= 17 and not vt.is_busy, 180)
wait(lambda: not wb_brain.busy(), 300)
used = [n for n, _ in getattr(vt.bot, "last_steps", [])]
record("voice: remembers into the brain", ok and "remember" in used and "Anna" in wiki_text(),
       f"tools {used}")

# AI activity: every job above was recorded as a run of steps
import wb_trace  # noqa: E402
from ai_activity import AiActivityWindow  # noqa: E402

runs = wb_trace.runs()
spoken = next((r for r in runs if r.source == "chat" and any(sp.kind == "stt" for sp in r.spans)), None)
kinds = [sp.kind for sp in spoken.spans] if spoken else []
model = next((sp for sp in spoken.spans if sp.kind == "model"), None) if spoken else None
record("activity: a spoken question is recorded as speech to text, recall and model steps",
       bool(spoken) and "stt" in kinds and "model" in kinds and spoken.title != "Chat: (listening)" and
       bool(model and model.detail.get("tokens_out")), f"{spoken.title if spoken else '-'}: {kinds}")
dog = next((r for r in runs if "dog" in r.title.lower() and r.source == "chat"), None)
recall = next((sp for sp in dog.spans if sp.kind == "recall"), None) if dog else None
record("activity: the brain recall step says which pages it used",
       bool(recall and any("wiki/" in x for x in recall.detail.get("sources", []))),
       str(recall.detail.get("sources") if recall else "no recall step"))
expert_run = next((r for r in runs if r.source == "chat" and any(sp.kind == "route" and "Straight to" in sp.name
                                                                 for sp in r.spans)), None)
record("activity: an expert answer shows who was chosen and why",
       bool(expert_run) and any(sp.kind == "route" and sp.detail.get("why") for sp in expert_run.spans),
       str([(sp.kind, sp.name) for sp in expert_run.spans] if expert_run else "no expert run"))
record("activity: filing a note runs on its own, with the writing expert inside",
       any(r.source == "filing" and any(sp.kind == "model" for sp in r.spans) for r in runs),
       str(sorted({r.source for r in runs})))
aw = AiActivityWindow()
aw.show()
aw._pick_run(dog.id if dog else runs[-1].id, manual=True)
if recall:
    aw._pick_span(recall.id)
app.processEvents()
record("activity window: lists the jobs and shows a step's details",
       aw.list.count() == len(wb_trace.runs()) and len(aw.flow.boxes) >= 2 and
       ("Brain recall" in aw.details.toPlainText() or not recall), f"{aw.list.count()} jobs, {len(aw.flow.boxes)} steps")
aw.grab()
aw.close()

# A stuck question: Stop waiting gives up on it, and the next question still gets an answer
import threading  # noqa: E402

release = threading.Event()
stuck_bot = vt.bot
stuck_bot.reply = lambda text, lang=None, **kw: (release.wait(60), "too late")[1]
vt.run_turn(text="This one gets stuck.")
wait(lambda: vt.is_busy and vt.pending == 1, 5)
time.sleep(1)
vt.cancel_btn.click()
gone = not vt.is_busy and vt.pending == 0
vt.run_turn(text="Say the word banana.")
ok = wait(lambda: any(w == "bot" and "banana" in t.lower() for w, t, _ in vt.turns[-1:]), 120)
release.set()
wait(lambda: False, 1)  # let the stuck thread finish: its late "too late" must be ignored
late = any("too late" in t for _, t, _ in vt.turns)
record("voice: Stop waiting gives up a stuck question, the next one works",
       gone and ok and not late, f"bar gone {gone}, answered {ok}, late answer shown {late}")
vt._set_step = real_set_step

# Wake word: spoken audio fed in real time as if from the microphone
import numpy as np  # noqa: E402

s.wake_chime = False
vt.wake_uses_microphone = False
vt.set_mode("wake")
wait(lambda: vt.wake is not None, 60)


def to16k(a, r):
    n = int(len(a) * 16000 / r)
    return np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a.astype("float32") / 32768).astype("float32")


def feed(a):
    a = np.concatenate([np.zeros(12800, "float32"), a, np.zeros(24000, "float32")])
    a = a + np.random.default_rng(0).normal(0, 0.004, len(a)).astype("float32")
    # Through the microphone callback, with ONE buffer that is overwritten each block, like
    # sounddevice does. (Keeping views of that buffer once made every phrase silent.)
    block = np.zeros((480, 1), "float32")
    for i in range(0, len(a) - 480, 480):
        block[:, 0] = a[i:i + 480]
        vt.wake._callback(block, 480, None, None)
        if i % 1440 == 0:
            wait(lambda: False, 0.03)


before = len(vt.turns)
feed(to16k(*vt.bot.synth("Hey Luna, what is the capital of Norway?", "en")))
ok = wait(lambda: len(vt.turns) >= before + 2 and not vt.wake.paused, 120)
new = vt.turns[before:]
record("wake word: 'Hey Luna, what is the capital of Norway?'",
       ok and "luna" not in new[0][1].lower() and "oslo" in new[1][1].lower(), f"{[t[1] for t in new]}")
before = len(vt.turns)
feed(to16k(*vt.bot.synth("The weather tomorrow will be cloudy with some rain.", "en")))
wait(lambda: False, 6)
record("wake word: ignores speech without it", len(vt.turns) == before, vt.heard_label.text()[:80])

# Conversation mode: no wake word, "stop listening" ends it, and so does silence
vt.set_mode("conversation")
wait(lambda: vt.wake.conversation and not vt.wake.paused, 10)
before = len(vt.turns)
feed(to16k(*vt.bot.synth("What is the capital of Sweden?", "en")))
ok = wait(lambda: len(vt.turns) >= before + 2 and not vt.wake.paused, 120)
new = vt.turns[before:]
record("conversation: a question without the wake word is answered",
       ok and "stockholm" in new[1][1].lower(), f"{[t[1] for t in new]}")
before = len(vt.turns)
feed(to16k(*vt.bot.synth("And what about Denmark?", "en")))
ok = wait(lambda: len(vt.turns) >= before + 2 and not vt.wake.paused, 120)
new = vt.turns[before:]
record("conversation: a follow-up keeps the context", ok and "copenhagen" in new[1][1].lower(),
       f"{[t[1] for t in new]}")
feed(to16k(*vt.bot.synth("Stop listening.", "en")))
ok = wait(lambda: vt.mode == "wake", 60)
record("conversation: 'stop listening' switches back to Hey Luna", ok, vt.mode)
vt.set_mode("conversation")
vt.wake.idle_limit = 3
wait(lambda: vt.wake.conversation, 5)
quiet = np.zeros(480, "float32")
for _ in range(int(5 / 0.03)):  # five seconds of silence through the microphone callback
    vt.wake._callback(quiet.reshape(-1, 1), 480, None, None)
    wait(lambda: False, 0.03)
record("conversation: ends by itself after the silence limit", vt.mode == "wake", vt.mode)
vt.next_mode()
vt.next_mode()
record("mode button cycles Hey Luna -> Conversation -> Hold to talk", vt.mode == "hold" and vt.wake is None,
       vt.mode_btn.text())

# 5. Research (uses the web)
if "--skip-research" not in sys.argv:
    from tab_research import ResearchTab  # noqa: E402

    rt = ResearchTab(s, status)
    rt.question.setText("What is the latest stable version of Python?")
    rt.start()
    ok = wait(lambda: rt.go.isEnabled() and rt.history.count() > 0, 300)
    record("research: report saved and shown", ok and len(rt.report.toPlainText()) > 100,
           rt.check_label.text()[:120])
    record("research: report says who did the work", "Who did the work" in rt.report.toPlainText(),
           next((l for l in rt.report.toPlainText().splitlines() if "Who did the work" in l), "")[:160])

# 6. Evaluation tab: run the quick invoices suite through promptfoo
from tab_eval import EvalTab  # noqa: E402

ev = EvalTab(s, status)
ev.run_suite("invoices.yaml")
ok = wait(lambda: all(b.isEnabled() for b in ev.buttons) and "passed" in ev.summary.text(), 240)
record("evaluation: invoices suite", ok and ev.summary.text().startswith("5 of 5"), ev.summary.text())

# 7. The VS Code bridge: answers this PC with the token only, and the extension package builds
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402

import wb_bridge  # noqa: E402
import wb_vscode_ext  # noqa: E402

s.bridge_port = 8796
br = wb_bridge.Bridge(s)
br.start()


def bridge_get(headers):
    try:
        with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8796/api/health", headers=headers),
                                    timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


tok = {"Authorization": f"Bearer {wb_bridge.token()}"}
record("vs code bridge: answers with the token, refuses without it and from web pages",
       bridge_get(tok) == 200 and bridge_get({}) == 401 and bridge_get({**tok, "Origin": "https://x.example"}) == 403)
body = json.dumps({"messages": [{"role": "user", "content": "What does sq(4) return?"}],
                   "context": {"file": "sq.py", "language": "python", "selection": "def sq(x):\n    return x * x\n"}})
with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8796/api/chat", body.encode(),
                            {**tok, "Content-Type": "application/json"}), timeout=300) as r:
    events = [json.loads(l) for l in r.read().decode().splitlines() if l.strip()]
answer = "".join(e.get("text", "") for e in events if e["type"] == "text")
record("vs code bridge: the code expert answers on this PC, streamed", "16" in answer and events[-1]["type"] == "done"
       and "on this PC" in events[-1]["by"], f"{events[-1].get('by')}: {answer[:60]}")
br.stop()
import zipfile  # noqa: E402

vsix = wb_vscode_ext.build(TMP)
names = zipfile.ZipFile(vsix).namelist()
import wb_code  # noqa: E402

if wb_code.vscode():  # code.cmd through cmd /c broke on the spaces in "Microsoft VS Code" and "AI Workbench"
    cli, _env = wb_code.code_cli()
    record("vs code: its command line runs without cmd.exe, so paths with spaces work",
           cli[0].lower().endswith("code.exe") and cli[-1].lower().endswith("cli.js")
           and all(Path(c).exists() for c in cli), str(cli))
record("vs code extension: package builds", {"extension.vsixmanifest", "extension/package.json",
                                             "extension/extension.js", "extension/media/chat.js"} <= set(names),
       vsix.name)

# 8. The window: abilities, the conversation and every panel open beside it
import wb_window  # noqa: E402

s.check_updates_at_start = False  # an updates dialog would wait for a click
w = wb_window.MainWindow(s)
w.show()
opened = []
for key in w.panels:
    w.open_panel(key)
    app.processEvents()
    opened.append(w.panel_area.stack.currentWidget() is w.panels[key][1] and w.panel_area.isVisible())
w.abilities.boxes["tv"].setChecked(False)
off = "tv" not in wb_abilities.enabled(s) and "tv" not in w.chat.on_groups()
w.abilities.boxes["tv"].setChecked(True)
record("window: every panel opens beside the conversation; Access and Evaluation are in Settings",
       all(opened) and w.settings_panel.indexOf(w.access) >= 0 and w.settings_panel.indexOf(w.evaluation) >= 0,
       str(dict(zip(w.panels, opened))))
record("window: switching an ability off takes its tools away", off)
w.close()

vt.shutdown()
inv.shutdown()
ct.shutdown()
record("no error dialogs", not dialogs, "; ".join(dialogs)[:200])
failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} passed. Test folder: {TMP}")
if not failed:
    shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if failed else 0)
