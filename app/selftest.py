"""End-to-end self-test: drives the real tabs with test data in a temporary folder.

Run: .venv\\Scripts\\python.exe app\\selftest.py [--skip-research]
Needs Ollama with the default models. Uses its own settings file, so your settings are untouched.
"""
from __future__ import annotations

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
s.ensure_folders()
s.apply()
import wb_access  # noqa: E402

wb_access.configure(s)  # default answer to any permission question: no
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

# Experts: a code question goes to the code expert and says so; provider list saves
from tab_experts import ExpertsTab  # noqa: E402

ex = ExpertsTab(s, status)
ex.question.setPlainText("Write a Python function that returns the square of a number. Code only.")
ex.ask()
wait(lambda: not ex.busy, 300)
t = ex.turns[-1] if ex.turns else {"a": "", "credit": ""}
record("experts: code question -> code expert, with a credit line",
       "Code expert" in t["credit"] and "def " in t["a"], t["credit"].replace("\n", " ")[:120])
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
record("code: new project proposed as files", any(n.endswith(".py") for n in names), str(names))
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

# 4. Voice: one spoken turn from a test recording (no speakers needed)
import voice  # noqa: E402

from tab_voice import VoiceTab  # noqa: E402

wav = ROOT / "02-voice" / "test-audio" / "q-sv.wav"
vt = VoiceTab(s, status)
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
audio, rate = vt.bot.synth("Testing the voice.", "en")
record("voice: speech synthesis", len(audio) > rate * 0.5, f"{len(audio) / rate:.1f}s at {rate} Hz, speed 1.25")

# Busy bar: shows the steps while working, hides when done
steps = []
real_set_step = vt._set_step
vt._set_step = lambda text: (steps.append(text), real_set_step(text))
vt.run_turn(text="What is two plus two?")
shown = wait(lambda: vt.is_busy, 5)
ok = wait(lambda: len(vt.turns) >= 6 and not vt.is_busy, 120)
record("voice: busy bar shows steps and hides when done", shown and ok and "Thinking…" in steps,
       f"steps {list(dict.fromkeys(steps))}, state '{vt.state.text()}'")

# Brain in the voice assistant: recalls what it was told, and remembers new things
vt.run_turn(text="What is my dog called?")
ok = wait(lambda: len(vt.turns) >= 8 and not vt.is_busy, 180)
used = [n for n, _ in getattr(vt.bot, "last_steps", [])]
record("voice: recalls from the brain", ok and "pixel" in vt.turns[-1][1].lower() and "recall" in used,
       f"{vt.turns[-1][1][:80]} · tools {used}")
vt.run_turn(text="Please remember that my sister Anna's birthday is on the third of March.")
ok = wait(lambda: len(vt.turns) >= 10 and not vt.is_busy, 180)
wait(lambda: not wb_brain.busy(), 300)
used = [n for n, _ in getattr(vt.bot, "last_steps", [])]
record("voice: remembers into the brain", ok and "remember" in used and "Anna" in wiki_text(),
       f"tools {used}")

# A stuck question: Stop waiting gives up on it, and the next question still gets an answer
import threading  # noqa: E402

release = threading.Event()
stuck_bot = vt.bot
stuck_bot.reply = lambda text, lang=None: (release.wait(60), "too late")[1]
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

vt.shutdown()
inv.shutdown()
ct.shutdown()
record("no error dialogs", not dialogs, "; ".join(dialogs)[:200])
failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} passed. Test folder: {TMP}")
if not failed:
    shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if failed else 0)
