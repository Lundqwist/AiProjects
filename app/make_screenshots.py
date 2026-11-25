"""Screenshots of the conversation and every panel for the README, made with the made-up test data (never your own).

Run: .venv\\Scripts\\python.exe app\\make_screenshots.py [out-folder] [--skip-research]
Uses its own settings file and folders in a demo folder, so your settings and data are untouched.
Needs Ollama with the default models (and the web for the research tab). Default out-folder: docs\\screenshots
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
args = [a for a in sys.argv[1:] if not a.startswith("--")]
OUT = Path(args[0]) if args else ROOT / "docs" / "screenshots"
# a short, neutral folder: its path shows in the Settings tab
DEMO = Path(os.environ.get("AI_WORKBENCH_DEMO", Path(ROOT.anchor) / "AI Workbench demo"))
shutil.rmtree(DEMO, ignore_errors=True)
DEMO.mkdir(parents=True)
(DEMO / "settings.json").write_text(json.dumps({
    "rag_db": str(DEMO / "Document database"),
    "invoice_inbox": str(DEMO / "Invoices" / "Inbox"),
    "invoice_output": str(DEMO / "Invoices" / "Output"),
    "reports_dir": str(DEMO / "Research reports"),
    "creations_dir": str(DEMO / "Creations"),   # never your own pictures, songs, projects or notes
    "code_projects_dir": str(DEMO / "Code projects"),
    "code_roots": [str(DEMO / "Code projects")],
    "brain_dir": str(DEMO / "Brain"),
    "conversations_dir": str(DEMO / "Conversations"),
    "speak_replies": False,
    "voice_mode": "hold",
    "check_updates_at_start": False,
    "bridge_port": 8794,
    "tv_name": "Living room TV",
    "tv_favorites": [{"name": "News", "app": "Samsung TV Plus", "channel": "4556"},
                     {"name": "YouTube", "app": "YouTube", "channel": ""}],
    "allowed_programs": ["Google Chrome", "Spotify", "Steam", "Discord"],
    "settings_version": 2,
}), encoding="utf-8")
os.environ["AI_WORKBENCH_SETTINGS"] = str(DEMO / "settings.json")
os.environ.pop("VIKUNJA_TOKEN", None)
sys.argv = [sys.argv[0], "--no-update-check"]
sys.path.insert(0, str(APP_DIR))

import main  # noqa: E402  (loads the demo settings)
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication(sys.argv)
app.setStyleSheet(main.THEME)
for name in ("warning", "information"):
    setattr(QMessageBox, name, lambda *a, **k: QMessageBox.Ok)
QMessageBox.question = lambda *a, **k: QMessageBox.No

main.settings.ensure_folders()
main.settings.apply()
os.environ.pop("VIKUNJA_TOKEN", None)
w = main.MainWindow(main.settings)
w.resize(1500, 860)
w.show()


def wait(cond, timeout=300):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.05)
    return False


def step(msg):
    print(msg, flush=True)


step("documents")
w.documents.ingest(sorted((ROOT / "05-eval" / "fixtures" / "handbook").glob("*.md")))
wait(lambda: w.documents.sources.count() == 2, 120)
w.documents.question.setText("How many vacation days do employees get, and how do I book them?")
w.documents.ask()
wait(lambda: not w.documents.busy, 180)

step("invoices")
sys.path.insert(0, str(ROOT / "03-invoices"))
import make_samples  # noqa: E402

samples = DEMO / "samples"
samples.mkdir()
make_samples.INBOX = samples
for sample in make_samples.SAMPLES:
    make_samples.make(*sample)
w.invoices.copy_to_inbox(sorted(samples.glob("*.pdf")))
wait(lambda: not w.invoices.queued and w.invoices.table.rowCount() + w.invoices.review.count() >= len(make_samples.SAMPLES), 400)
if w.invoices.review.count():
    w.invoices.review.setCurrentRow(0)

step("conversation")
w.chat.run_turn(text="What is the capital of Norway?")
wait(lambda: len(w.chat.turns) >= 2 and not w.chat.is_busy, 120)
w.chat.run_turn(text="Och vad heter Sveriges huvudstad?")
wait(lambda: len(w.chat.turns) >= 4 and not w.chat.is_busy, 120)
w.chat.run_turn(text="Write a Python function that checks if a number is prime.")
wait(lambda: len(w.chat.turns) >= 6 and not w.chat.is_busy, 300)

if "--skip-research" not in sys.argv:
    step("research")
    w.research.question.setText("What is new in the latest stable version of Python?")
    w.research.start()
    wait(lambda: w.research.go.isEnabled() and w.research.history.count() > 0, 400)

step("evaluation")
w.evaluation.run_suite("invoices.yaml")
wait(lambda: all(b.isEnabled() for b in w.evaluation.buttons) and "passed" in w.evaluation.summary.text(), 300)

OUT.mkdir(parents=True, exist_ok=True)
shots = [("conversation", None), ("documents", "documents"), ("invoices", "invoices"), ("research", "research"),
         ("tv", "tv"), ("access", "access"), ("evaluation", "evaluation"), ("settings", "settings"),
         ("create", "create"), ("code", "code"), ("brain", "brain")]
for i, (name, key) in enumerate(shots):
    if key is None:
        w.close_panel()
    elif key == "evaluation":
        w.open_tab("Evaluation")
    else:
        w.open_panel(key)
    wait(lambda: False, 1.0)
    w.grab().save(str(OUT / f"{i}-{name}.png"))
    step(f"saved {OUT / f'{i}-{name}.png'}")

w.chat.shutdown()
w.invoices.shutdown()
w.evaluation.shutdown()
shutil.rmtree(DEMO, ignore_errors=True)
