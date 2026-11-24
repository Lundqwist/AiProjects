"""AI Workbench: one Windows program for the five local AI projects.

Run: .venv\\Scripts\\pythonw.exe app\\main.py   (or the Start menu shortcut)
"""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
sys.path[:0] = [str(APP_DIR)] + [str(ROOT / d) for d in ("01-rag", "02-voice", "03-invoices", "04-research")]
warnings.filterwarnings("ignore")

from wb_settings import SETTINGS_DIR, Settings  # noqa: E402

settings = Settings.load()
os.environ["OLLAMA_HOST"] = settings.ollama_host  # must be set before ollama is imported

# pythonw has no console: send stray output and errors to a log file
if sys.stdout is None or sys.stderr is None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    log = open(SETTINGS_DIR / "workbench.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = sys.stderr = log

from PySide6.QtCore import QByteArray, QTimer  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QTabWidget  # noqa: E402

import ollama  # noqa: E402
from tab_documents import DocumentsTab  # noqa: E402
from tab_eval import EvalTab  # noqa: E402
from tab_invoices import InvoicesTab  # noqa: E402
from tab_research import ResearchTab  # noqa: E402
from tab_settings import SettingsTab  # noqa: E402
from tab_tv import TvTab  # noqa: E402
from tab_voice import VoiceTab  # noqa: E402
import wb_updates  # noqa: E402
from updates_dialog import UpdatesDialog  # noqa: E402
from wb_common import THEME, run_in_background  # noqa: E402


class MainWindow(QMainWindow):
    def __init__(self, s: Settings):
        super().__init__()
        self.s = s
        self.setWindowTitle("AI Workbench")
        icon = APP_DIR / "icon.ico"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1200, 800)
        if s.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(s.window_geometry.encode()))

        self.ollama_label = QLabel("Ollama: checking…")
        self.statusBar().addPermanentWidget(self.ollama_label)
        status = lambda msg: self.statusBar().showMessage(str(msg), 8000)  # noqa: E731

        self.tabs = QTabWidget()
        self.documents = DocumentsTab(s, status)
        self.voice = VoiceTab(s, status)
        self.invoices = InvoicesTab(s, status)
        self.research = ResearchTab(s, status)
        self.evaluation = EvalTab(s, status)
        self.tv = TvTab(s, status)
        self.settings_tab = SettingsTab(s)
        self.settings_tab.saved.connect(self.settings_saved)
        for widget, name in ((self.documents, "Documents"), (self.voice, "Voice"),
                             (self.invoices, "Invoices"), (self.research, "Research"),
                             (self.tv, "TV"), (self.evaluation, "Evaluation"), (self.settings_tab, "Settings")):
            self.tabs.addTab(widget, name)
        self.tabs.setCurrentIndex(min(s.last_tab, self.tabs.count() - 1))
        self.setCentralWidget(self.tabs)

        self.settings_tab.check_now.connect(lambda: self.check_updates(manual=True))
        self.check_ollama()
        self.ollama_timer = QTimer(self, interval=30000, timeout=self.check_ollama)
        self.ollama_timer.start()
        if s.check_updates_at_start and "--no-update-check" not in sys.argv:
            QTimer.singleShot(1500, self.check_updates)

    def check_updates(self, manual: bool = False):
        self.statusBar().showMessage("Checking for updates…")

        def show(updates):
            # optional updates the user said "not now" to stay hidden until a newer version appears
            shown = [u for u in updates if u.required or manual or u.skip_key not in self.s.skipped_updates]
            if not shown:
                self.statusBar().showMessage("Everything is up to date", 8000)
                return
            self.statusBar().clearMessage()
            dlg = UpdatesDialog(self, self.s, shown)
            if dlg.exec() == 2:  # updates scheduled for after a restart
                self.close()
                return
            self.settings_tab.load_values()  # e.g. a newly downloaded voice
            self.voice.apply_settings()
            self.check_ollama()

        run_in_background(wb_updates.check_all, self.s, on_progress=lambda m: self.statusBar().showMessage(m),
                          on_result=show,
                          on_error=lambda e: self.statusBar().showMessage(f"Update check failed: {e}", 10000))

    def settings_saved(self):
        self.voice.apply_settings()
        self.documents.refresh_sources()
        self.invoices.refresh()
        self.research.load_history()
        self.statusBar().showMessage("Settings saved", 5000)
        self.check_ollama()

    def check_ollama(self):
        needed = {self.s.chat_model, self.s.vision_model, self.s.embed_model, self.s.agent_model}

        def work(emit):
            return {m.model for m in ollama.list().models}

        def show(models: set):
            names = models | {m.removesuffix(":latest") for m in models}
            missing = sorted(n for n in needed if n not in names and f"{n}:latest" not in models)
            if missing:
                self.ollama_label.setText(f"<span style='color:#ff8a80'>Ollama: missing {', '.join(missing)} "
                                          f"(run: ollama pull {missing[0]})</span>")
            else:
                self.ollama_label.setText(f"<span style='color:#9be59b'>● Ollama</span> · {len(models)} models")

        run_in_background(work, on_result=show,
                          on_error=lambda e: self.ollama_label.setText(
                              "<span style='color:#ff8a80'>● Ollama is not running. Start it from the Start menu.</span>"))

    def closeEvent(self, event):
        self.s.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.s.last_tab = self.tabs.currentIndex()
        try:
            self.s.save()
        except OSError:
            pass
        for tab in (self.voice, self.invoices, self.evaluation):
            tab.shutdown()
        super().closeEvent(event)


def main() -> int:
    settings.ensure_folders()
    settings.apply()
    app = QApplication(sys.argv)
    app.setApplicationName("AI Workbench")
    app.setStyleSheet(THEME)
    if "--screenshot" in sys.argv:
        sys.argv.append("--no-update-check")
    w = MainWindow(settings)
    w.show()
    if "--screenshot" in sys.argv:  # used by tests: save a picture of each tab and quit
        out = Path(sys.argv[sys.argv.index("--screenshot") + 1])
        out.mkdir(parents=True, exist_ok=True)

        def shoot(i=0):
            if i >= w.tabs.count():
                app.quit()
                return
            w.tabs.setCurrentIndex(i)
            QTimer.singleShot(1500, lambda: (w.grab().save(str(out / f"{i}-{w.tabs.tabText(i)}.png")),
                                             shoot(i + 1)))

        QTimer.singleShot(2500, shoot)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
