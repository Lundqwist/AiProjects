"""End-to-end self-test: drives the real tabs with test data in a temporary folder.

Run: .venv\\Scripts\\python.exe app\\selftest.py [--skip-research]
Needs Ollama with the default models. Uses its own settings file, so your settings are untouched.
"""
from __future__ import annotations

import os
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
s.speak_replies = False
s.ensure_folders()
s.apply()
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

# Wake word: spoken audio fed in real time as if from the microphone
import numpy as np  # noqa: E402

s.wake_chime = False
vt.wake_uses_microphone = False
vt.wake_btn.setChecked(True)
wait(lambda: vt.wake is not None, 60)


def to16k(a, r):
    n = int(len(a) * 16000 / r)
    return np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a.astype("float32") / 32768).astype("float32")


def feed(a):
    a = np.concatenate([np.zeros(12800, "float32"), a, np.zeros(24000, "float32")])
    a = a + np.random.default_rng(0).normal(0, 0.004, len(a)).astype("float32")
    for i in range(0, len(a), 1600):
        vt.wake.feed(a[i:i + 1600])
        wait(lambda: False, 0.1)


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
vt.wake_btn.setChecked(False)

# 5. Research (uses the web)
if "--skip-research" not in sys.argv:
    from tab_research import ResearchTab  # noqa: E402

    rt = ResearchTab(s, status)
    rt.question.setText("What is the latest stable version of Python?")
    rt.start()
    ok = wait(lambda: rt.go.isEnabled() and rt.history.count() > 0, 300)
    record("research: report saved and shown", ok and len(rt.report.toPlainText()) > 100,
           rt.check_label.text()[:120])

# 6. Evaluation tab: run the quick invoices suite through promptfoo
from tab_eval import EvalTab  # noqa: E402

ev = EvalTab(s, status)
ev.run_suite("invoices.yaml")
ok = wait(lambda: all(b.isEnabled() for b in ev.buttons) and "passed" in ev.summary.text(), 240)
record("evaluation: invoices suite", ok and ev.summary.text().startswith("5 of 5"), ev.summary.text())

vt.shutdown()
inv.shutdown()
record("no error dialogs", not dialogs, "; ".join(dialogs)[:200])
failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} passed. Test folder: {TMP}")
if not failed:
    shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if failed else 0)
