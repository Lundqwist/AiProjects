"""AI Workbench's main window: every tab, the status bar, update and Ollama checks.

Kept apart from main.py on purpose: importing this pulls in all the heavy parts (chromadb, torch,
Whisper, ...), which after a reboot can take minutes. main.py shows a loading window first and
imports this module afterwards.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QPushButton, QTabWidget, QVBoxLayout, QWidget

from wb_settings import Settings
from wb_home import HeaderBand, HomePage

import ollama
from tab_documents import DocumentsTab
from tab_eval import EvalTab
from tab_experts import ExpertsTab
from tab_create import CreateTab
from tab_code import CodeTab
from tab_invoices import InvoicesTab
from tab_research import ResearchTab
from tab_settings import SettingsTab
from tab_tv import TvTab
from tab_access import AccessTab
from tab_brain import BrainTab
import wb_brain
import sounddevice as sd
import wb_access
import wb_wake
from wb_permission import PermissionBroker
from tab_voice import VoiceTab
import wb_updates
from updates_dialog import UpdatesDialog
from wb_common import GOLD, run_in_background
import wb_trace
from ai_activity import AiActivityWindow


APP_DIR = Path(__file__).resolve().parent


class TraceBridge(QObject):
    changed = Signal()  # a job started or ended, on whichever thread: handled on the window's


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
        # AI activity: what the AI is doing, step by step (lit up while it works)
        self.activity_btn = QPushButton("AI activity")
        self.activity_btn.setToolTip("See each step the AI takes: listening, recall, models, tools, speaking")
        self.activity_btn.clicked.connect(self.show_activity)
        self.activity_window = None
        self.statusBar().addPermanentWidget(self.activity_btn)
        self.statusBar().addPermanentWidget(self.ollama_label)
        self._trace_bridge = TraceBridge()
        self._trace_bridge.changed.connect(self._activity_light)
        wb_trace.subscribe(lambda ev, obj: self._trace_bridge.changed.emit() if ev == "run" else None)
        status = lambda msg: self.statusBar().showMessage(str(msg), 8000)  # noqa: E731

        self.tabs = QTabWidget()
        # the front page, as in StoryMaker: the Lundqwist emblem and a card per tool
        self.home = HomePage([
            ("Brain", "What the assistant remembers: notes, a wiki and rules, on this PC"),
            ("Documents", "Chat with your own PDFs and notes, with sources"),
            ("Experts", "Code, reasoning and writing experts, online AI for big jobs"),
            ("Create", "Pictures, music and stories, saying who made them"),
            ("Code", "New projects, explanations and fixes, opened in VS Code"),
            ("Voice", "Talk with the assistant: \"Hey Luna\", in English or Swedish"),
            ("Invoices", "Invoice PDFs and photos into checked CSV rows"),
            ("Research", "A cited report from the web, with a fact check"),
            ("TV", "Wake the TV, favourite channels and a remote"),
            ("Access", "What the assistant may do on this PC"),
            ("Evaluation", "Run the test suites and see what failed"),
            ("Settings", "Models, folders, sound devices and voices"),
        ])
        self.home.open_tab.connect(self.open_tab)
        self.tabs.addTab(self.home, "Home")
        self.brain = BrainTab(s, status)
        self.documents = DocumentsTab(s, status)
        self.experts = ExpertsTab(s, status)
        self.create = CreateTab(s, status)
        self.code = CodeTab(s, status)
        self.voice = VoiceTab(s, status)
        self.invoices = InvoicesTab(s, status)
        self.research = ResearchTab(s, status)
        self.evaluation = EvalTab(s, status)
        self.tv = TvTab(s, status)
        # Questions from the assistant's tools ("open Notepad?") come up on screen, with a chime
        self.broker = PermissionBroker(self, on_ask=lambda: sd.play(wb_wake.chime(), wb_wake.RATE,
                                                                     device=s.output_device))
        wb_access.configure(s, self.broker.ask)
        self.access = AccessTab(s, status)
        self.settings_tab = SettingsTab(s)
        self.settings_tab.saved.connect(self.settings_saved)
        for widget, name in ((self.brain, "Brain"), (self.documents, "Documents"), (self.experts, "Experts"), (self.create, "Create"),
                             (self.code, "Code"), (self.voice, "Voice"),
                             (self.invoices, "Invoices"), (self.research, "Research"),
                             (self.tv, "TV"), (self.access, "Access"), (self.evaluation, "Evaluation"),
                             (self.settings_tab, "Settings")):
            self.tabs.addTab(widget, name)
        self.tabs.setCurrentIndex(0)  # always start on the front page

        def to_voice(question: str):
            self.tabs.setCurrentWidget(self.voice)
            self.voice.run_turn(text=question)
        self.experts.hand_off = to_voice
        header = HeaderBand("Local AI with Ollama  ·  lundqwist.se")
        header.home_clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(header)
        lay.addWidget(self.tabs, 1)
        self.setCentralWidget(central)

        # the Brain tab shows what the voice assistant wrote down meanwhile
        self.tabs.currentChanged.connect(lambda i: self.brain.refresh() if self.tabs.widget(i) is self.brain
                                         and not self.brain.busy else None)
        # once a day, corrections you repeated become rules (checked a while after start, then hourly)
        self.learn_timer = QTimer(self, interval=3600_000, timeout=self.learn_rules)
        self.learn_timer.start()
        QTimer.singleShot(120_000, self.learn_rules)

        self.settings_tab.check_now.connect(lambda: self.check_updates(manual=True))
        self.check_ollama()
        self.ollama_timer = QTimer(self, interval=30000, timeout=self.check_ollama)
        self.ollama_timer.start()
        if s.check_updates_at_start and "--no-update-check" not in sys.argv:
            QTimer.singleShot(1500, self.check_updates)

    def learn_rules(self):
        if self.brain.busy or not wb_brain.learning_due():
            return

        def done(rules):
            if rules:
                self.statusBar().showMessage(f"Brain: learned {len(rules)} new rule(s) from your corrections "
                                             "(see Brain → Rules)", 15000)
            self.brain.refresh()

        run_in_background(wb_brain.learn_rules, on_result=done,
                          on_error=lambda e: print(f"brain: rule learning failed: {e}"))

    def show_activity(self):
        if self.activity_window is None:
            def on_map(sources):
                self.brain.show_map().highlight(sources)
            self.activity_window = AiActivityWindow(self, show_on_map=on_map)
            self.activity_window.setAttribute(Qt.WA_DeleteOnClose)
            self.activity_window.finished.connect(lambda _: setattr(self, "activity_window", None))
        self.activity_window.show()
        self.activity_window.raise_()
        self.activity_window.activateWindow()
        return self.activity_window

    def _activity_light(self):
        working = wb_trace.busy()
        self.activity_btn.setText("◉ AI working…" if working else "AI activity")
        self.activity_btn.setStyleSheet(f"color: {GOLD}; border-color: {GOLD};" if working else "")

    def bring_to_front(self):
        """Called when AI Workbench is started again while this copy runs: show this window instead."""
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def open_tab(self, name: str):
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == name:
                self.tabs.setCurrentIndex(i)
                return

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
        self.experts.apply_settings()
        self.create.refresh()
        self.code.refresh_files()
        self.documents.refresh_sources()
        self.invoices.refresh()
        self.research.load_history()
        self.brain.refresh()
        self.statusBar().showMessage("Settings saved", 5000)
        self.check_ollama()

    def check_ollama(self):
        needed = {self.s.chat_model, self.s.vision_model, self.s.embed_model, self.s.agent_model,
                  self.s.code_model, self.s.reasoning_model, self.s.writing_model} - {""}

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
        try:
            self.s.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
            self.s.last_tab = self.tabs.currentIndex()
            self.s.save()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        # Every part gets its chance to stop, even if one before it fails (a microphone that
        # won't stop used to raise here and leave the rest running)
        for tab in (self.voice, self.invoices, self.evaluation, self.create):
            try:
                tab.shutdown()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        super().closeEvent(event)
        QApplication.instance().quit()  # also when a dialog or other window is still open
