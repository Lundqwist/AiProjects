"""AI Workbench's main window: the abilities on the left, the one conversation in the middle, and the
tool panel you open on the right.

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
from PySide6.QtWidgets import (QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QPushButton, QSplitter, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from wb_settings import Settings
from wb_home import HeaderBand

import ollama
from tab_documents import DocumentsTab
from tab_eval import EvalTab
from tab_create import CreateTab
from tab_code import CodeTab
from tab_invoices import InvoicesTab
from tab_research import ResearchTab
from tab_settings import SettingsTab
from tab_tv import TvTab
from tab_access import AccessTab
from tab_brain import BrainTab
import wb_abilities
import wb_brain
import sounddevice as sd
import wb_access
import wb_bridge
import wb_spotify
import wb_task_tools
import wb_wake
from wb_permission import PermissionBroker
from tab_chat import ChatTab
from conversations_pane import ConversationsPane
import wb_conversations
import wb_updates
from updates_dialog import UpdatesDialog
from wb_common import GOLD, MUTED, NAVY_2, NAVY_3, run_in_background
import wb_trace
from ai_activity import AiActivityWindow


APP_DIR = Path(__file__).resolve().parent


class TraceBridge(QObject):
    changed = Signal()  # a job started or ended, on whichever thread: handled on the window's


class SettingsPanel(QTabWidget):
    """Settings, with what the assistant may do (Access) and the test suites (Evaluation) inside."""

    def __init__(self, s: Settings, status):
        super().__init__()
        self.settings_tab = SettingsTab(s)
        self.access = AccessTab(s, status)
        self.evaluation = EvalTab(s, status)
        self.addTab(self.settings_tab, "Settings")
        self.addTab(self.access, "Access")
        self.addTab(self.evaluation, "Evaluation")


class AbilitiesBar(QFrame):
    """The list on the left: tick what the assistant may use; Open shows the ability's full panel."""
    changed = Signal()
    open_panel = Signal(str, str)   # panel key, page

    def __init__(self, s: Settings):
        super().__init__()
        self.s = s
        self.setObjectName("abilities")
        self.setStyleSheet(f"QFrame#abilities {{ background: {NAVY_2}; border-right: 1px solid {NAVY_3}; }}"
                           " QFrame#abilities QLabel, QFrame#abilities QCheckBox { background: transparent; }")
        self.setMinimumWidth(210)
        self.setMaximumWidth(300)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 10, 12)
        lay.setSpacing(4)
        title = QLabel("Abilities")
        title.setStyleSheet(f"color: {GOLD}; font-size: 12pt; font-weight: 600;")
        lay.addWidget(title)
        hint = QLabel("Ticked ones the assistant may use. Open shows the full tool.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {MUTED}; font-size: 8.5pt;")
        lay.addWidget(hint)
        lay.addSpacing(6)
        self.boxes: dict[str, QCheckBox] = {}
        for key, a in wb_abilities.ABILITIES.items():
            row = QHBoxLayout()
            row.setSpacing(4)
            box = QCheckBox(a.label)
            box.setToolTip(a.about)
            box.toggled.connect(lambda on, k=key: self._toggled(k, on))
            self.boxes[key] = box
            row.addWidget(box, 1)
            if a.panel:
                btn = QPushButton("Open")
                btn.setFixedWidth(54)
                btn.setStyleSheet("padding: 2px 6px; font-size: 8.5pt;")
                btn.setToolTip(f"Show the {a.label} panel beside the conversation")
                btn.clicked.connect(lambda _=False, p=a.panel, pg=a.page: self.open_panel.emit(p, pg))
                row.addWidget(btn)
            lay.addLayout(row)
        lay.addStretch(1)
        settings = QPushButton("⚙  Settings")
        settings.setToolTip("Models, folders, sound, voices, online AI, Access, Evaluation and the VS Code extension")
        settings.clicked.connect(lambda: self.open_panel.emit("settings", ""))
        lay.addWidget(settings)
        self.lit: set[str] = set()
        self.unlit = QTimer(self, singleShot=True, interval=15000, timeout=lambda: self.light([]))
        self.refresh()

    def refresh(self):
        on = set(wb_abilities.enabled(self.s))
        spotify = wb_spotify.connected()
        for key, box in self.boxes.items():
            box.blockSignals(True)
            box.setChecked(key in on)
            box.blockSignals(False)
            ok, why = wb_abilities.ready(self.s, key, spotify)
            a = wb_abilities.ABILITIES[key]
            box.setToolTip(a.about + ("" if ok else f"\n{why}"))
            colour = GOLD if key in self.lit else ("#5a6b88" if not ok else "")
            box.setStyleSheet(f"color: {colour}; font-weight: {'600' if key in self.lit else 'normal'};"
                              if colour else "")

    def _toggled(self, key: str, on: bool):
        self.s.abilities = dict(self.s.abilities or {}, **{key: on})
        try:
            self.s.save()
        except OSError:
            pass
        self.changed.emit()

    def light(self, groups: list[str]):
        """Light up the abilities the last answer used, for a little while."""
        self.lit = {k for k, a in wb_abilities.ABILITIES.items() if set(a.groups) & set(groups)}
        self.refresh()
        if self.lit:
            self.unlit.start()


class PanelArea(QFrame):
    """The tool panel beside the conversation, with its name and a close button."""
    closed = Signal()

    def __init__(self):
        super().__init__()
        self.title = QLabel()
        self.title.setStyleSheet(f"color: {GOLD}; font-size: 12pt; font-weight: 600;")
        close = QPushButton("×")
        close.setFixedWidth(34)
        close.setStyleSheet("padding: 0px; font-size: 14pt;")
        close.setToolTip("Close the panel (the conversation stays)")
        close.clicked.connect(self.closed.emit)
        head = QHBoxLayout()
        head.addWidget(self.title, 1)
        head.addWidget(close)
        self.stack = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addLayout(head)
        lay.addWidget(self.stack, 1)


class MainWindow(QMainWindow):
    def __init__(self, s: Settings):
        super().__init__()
        self.s = s
        self.setWindowTitle("AI Workbench")
        icon = APP_DIR / "icon.ico"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1400, 860)
        if s.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(s.window_geometry.encode()))

        self.ollama_label = QLabel("Ollama: checking…")
        self.bridge_label = QLabel("")
        self.activity_btn = QPushButton("AI activity")
        self.activity_btn.setToolTip("See each step the AI takes: listening, routing, models, tools, speaking")
        self.activity_btn.clicked.connect(self.show_activity)
        self.activity_window = None
        self.statusBar().addPermanentWidget(self.bridge_label)
        self.statusBar().addPermanentWidget(self.activity_btn)
        self.statusBar().addPermanentWidget(self.ollama_label)
        self._trace_bridge = TraceBridge()
        self._trace_bridge.changed.connect(self._activity_light)
        wb_trace.subscribe(lambda ev, obj: self._trace_bridge.changed.emit() if ev == "run" else None)
        status = lambda msg: self.statusBar().showMessage(str(msg), 8000)  # noqa: E731

        # The tools, each a panel that opens beside the conversation
        self.brain = BrainTab(s, status)
        self.documents = DocumentsTab(s, status)
        self.create = CreateTab(s, status)
        self.code = CodeTab(s, status)
        self.invoices = InvoicesTab(s, status)
        self.research = ResearchTab(s, status)
        self.tv = TvTab(s, status)
        # Questions from the assistant's tools ("open Notepad?") come up on screen, with a chime
        self.broker = PermissionBroker(self, on_ask=lambda: sd.play(wb_wake.chime(), wb_wake.RATE,
                                                                     device=s.output_device))
        wb_access.configure(s, self.broker.ask)
        self.settings_panel = SettingsPanel(s, status)
        self.settings_tab = self.settings_panel.settings_tab
        self.access = self.settings_panel.access
        self.evaluation = self.settings_panel.evaluation
        self.settings_tab.saved.connect(self.settings_saved)
        self.chat = ChatTab(s, status)
        # The assistant's invoice and report tools refresh their panels (called from other threads)
        wb_task_tools.configure(s, on_invoices=lambda: QTimer.singleShot(0, self.invoices, self.invoices.refresh),
                                on_report=lambda p: QTimer.singleShot(0, self.research,
                                                                      lambda: self.research.load_history(select=p)))

        self.panels = {"create": ("Create", self.create), "code": ("Code", self.code),
                       "research": ("Research", self.research), "invoices": ("Invoices", self.invoices),
                       "documents": ("Documents", self.documents), "tv": ("My TV", self.tv),
                       "brain": ("Brain", self.brain), "settings": ("Settings", self.settings_panel),
                       "access": ("Settings", self.settings_panel)}
        self.panel_area = PanelArea()
        for widget in dict.fromkeys(w for _, w in self.panels.values()):
            self.panel_area.stack.addWidget(widget)
        self.panel_area.closed.connect(self.close_panel)
        self.abilities = AbilitiesBar(s)
        self.abilities.changed.connect(self.abilities_changed)
        self.abilities.open_panel.connect(self.open_panel)
        self.chat.abilities_used.connect(self.abilities.light)
        # Saved conversations on the far right: click one to continue it, Archive or Delete beside each
        self.conversations = ConversationsPane(s)
        self.conversations.open.connect(self.chat.open_conversation)
        self.conversations.new.connect(lambda: self.chat.new_conversation())
        self.conversations.archive.connect(self.archive_conversation)
        self.conversations.delete.connect(self.delete_conversation)
        self.conversations.hide_me.connect(lambda: self.show_conversations(False))
        self.chat.conversations_changed.connect(self.conversations.refresh)
        self.chat.toggle_list.connect(lambda: self.show_conversations(self.conversations.isHidden()))

        self.split = QSplitter(Qt.Horizontal)
        self.split.addWidget(self.abilities)
        self.split.addWidget(self.chat)
        self.split.addWidget(self.panel_area)
        self.split.addWidget(self.conversations)
        self.split.setStretchFactor(1, 3)
        self.split.setStretchFactor(2, 4)
        self.split.setStretchFactor(3, 0)
        self.split.setCollapsible(0, False)
        self.split.setCollapsible(1, False)
        header = HeaderBand("One conversation, by voice or text  ·  local AI with Ollama  ·  lundqwist.se")
        header.home_clicked.connect(self.close_panel)
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(header)
        lay.addWidget(self.split, 1)
        self.setCentralWidget(central)
        if s.panel_open in self.panels:
            self.open_panel(s.panel_open, "")
        else:
            self.panel_area.hide()
        self.conversations.refresh(self.chat.conv.id)
        self.show_conversations(s.conversations_shown, remember=False)

        # once a day, corrections you repeated become rules (checked a while after start, then hourly)
        self.learn_timer = QTimer(self, interval=3600_000, timeout=self.learn_rules)
        self.learn_timer.start()
        QTimer.singleShot(120_000, self.learn_rules)

        # VS Code talks to the workbench through a local server (wb_bridge.py)
        self.bridge = None
        self.start_bridge()

        self.settings_tab.check_now.connect(lambda: self.check_updates(manual=True))
        self.check_ollama()
        self.ollama_timer = QTimer(self, interval=30000, timeout=self.check_ollama)
        self.ollama_timer.start()
        if s.check_updates_at_start and "--no-update-check" not in sys.argv:
            QTimer.singleShot(1500, self.check_updates)

    # ---- panels and abilities -----------------------------------------------------------------
    def open_panel(self, key: str, page: str = ""):
        name, widget = self.panels[key]
        self.panel_area.title.setText(name)
        self.panel_area.stack.setCurrentWidget(widget)
        if key == "access":
            self.settings_panel.setCurrentWidget(self.access)
        elif key == "settings":
            self.settings_panel.setCurrentWidget(self.settings_tab)
        if key == "create" and page:
            for i in range(self.create.pages.count()):
                if self.create.pages.tabText(i) == page:
                    self.create.pages.setCurrentIndex(i)
        if key == "brain" and not self.brain.busy:
            self.brain.refresh()
        if self.panel_area.isHidden():
            self.panel_area.show()
            sizes = self.split.sizes()
            total = max(self.split.width(), 1200)
            side = sizes[0] or 230
            right = (sizes[3] or 260) if self.conversations.isVisible() else 0
            rest = total - side - right
            self.split.setSizes([side, rest * 4 // 9, rest * 5 // 9, right])
        self._remember_panel(key)

    def close_panel(self):
        self.panel_area.hide()
        self._remember_panel("")

    def _remember_panel(self, key: str):
        if self.s.panel_open != key:
            self.s.panel_open = key
            try:
                self.s.save()
            except OSError:
                pass

    # ---- saved conversations --------------------------------------------------------------------
    def show_conversations(self, on: bool, remember: bool = True):
        self.conversations.setVisible(on)
        self.chat.list_btn.setText("☰ Conversations" + ("" if on else " »"))
        self.chat.new_btn.setVisible(not on)  # the list has its own New conversation button
        if on and self.isVisible():
            sizes = self.split.sizes()
            if len(sizes) == 4 and sum(sizes) and sizes[3] < 200:
                sizes[1] = max(300, sizes[1] - (260 - sizes[3]))
                sizes[3] = 260
                self.split.setSizes(sizes)
        if remember and self.s.conversations_shown != on:
            self.s.conversations_shown = on
            try:
                self.s.save()
            except OSError:
                pass

    def archive_conversation(self, cid: str, archive: bool):
        is_open = cid == self.chat.conv.id
        if is_open and archive:
            if self.chat.busy_reason():
                self.statusBar().showMessage(self.chat.busy_reason(), 8000)
                return
            self.chat.save_conversation()
        if not wb_conversations.set_archived(self.s, cid, archive):
            self.statusBar().showMessage("That conversation could not be changed.", 8000)
            return
        if is_open and archive:  # put away: a fresh conversation takes its place
            self.chat.new_conversation(save_current=False)
        self.statusBar().showMessage("Archived: Show archived finds it again." if archive
                                     else "Restored to the list.", 6000)
        self.conversations.refresh(self.chat.conv.id)

    def delete_conversation(self, cid: str):
        is_open = cid == self.chat.conv.id
        if is_open and self.chat.busy_reason():
            self.statusBar().showMessage(self.chat.busy_reason(), 8000)
            return
        c = wb_conversations.load(self.s, cid)
        title = c.title if c else cid
        if QMessageBox.question(self, "Delete conversation",
                                f"Delete “{title}”?\n\nIt goes to the Recycle Bin, so it can be restored from there.",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            wb_conversations.delete(self.s, cid)
        except OSError as e:
            QMessageBox.warning(self, "Delete conversation", str(e))
            return
        if is_open:
            self.chat.new_conversation(save_current=False)
        self.statusBar().showMessage(f"Deleted “{title}” (it is in the Recycle Bin).", 8000)
        self.conversations.refresh(self.chat.conv.id)

    def abilities_changed(self):
        self.chat.apply_settings()
        if not self.chat.turns:
            self.chat.render()

    def open_tab(self, name: str):
        """Open a tool by its old tab name (kept for callers from before the one-conversation layout)."""
        key = name.lower()
        if key in self.panels:
            self.open_panel(key)
        elif name in ("Access", "Evaluation"):
            self.open_panel("settings")
            self.settings_panel.setCurrentWidget(self.access if name == "Access" else self.evaluation)
        else:  # Voice, Experts, Home: they are the conversation now
            self.close_panel()

    def bring_to_front(self):
        """Called when AI Workbench is started again while this copy runs: show this window instead."""
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    # ---- VS Code bridge -------------------------------------------------------------------------
    def start_bridge(self):
        if self.bridge:
            self.bridge.stop()
            self.bridge = None
        if not self.s.bridge_on:
            self.bridge_label.setText("")
            return
        try:
            self.bridge = wb_bridge.Bridge(self.s)
            self.bridge.start()
            self.bridge_label.setText(f"<span style='color:#9be59b'>●</span> VS Code bridge :{self.s.bridge_port}")
            self.bridge_label.setToolTip("The AI Workbench extension in VS Code talks to this program on "
                                         f"127.0.0.1:{self.s.bridge_port} (this PC only, with a token)")
        except OSError as e:
            self.bridge = None
            self.bridge_label.setText(f"<span style='color:#ff8a80'>● VS Code bridge off: {e}</span>")

    # ---- the rest --------------------------------------------------------------------------------
    def learn_rules(self):
        if self.brain.busy or not wb_brain.learning_due():
            return

        def done(rules):
            if rules:
                self.statusBar().showMessage(f"Brain: learned {len(rules)} new rule(s) from your corrections "
                                             "(open Brain → Rules)", 15000)
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
            self.chat.apply_settings()
            self.check_ollama()

        run_in_background(wb_updates.check_all, self.s, on_progress=lambda m: self.statusBar().showMessage(m),
                          on_result=show,
                          on_error=lambda e: self.statusBar().showMessage(f"Update check failed: {e}", 10000))

    def settings_saved(self):
        self.chat.apply_settings()
        self.create.refresh()
        self.code.refresh_files()
        self.documents.refresh_sources()
        self.invoices.refresh()
        self.research.load_history()
        self.brain.refresh()
        self.abilities.refresh()
        if self.bridge is None or not self.s.bridge_on or self.bridge.port != self.s.bridge_port:
            self.start_bridge()
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
            self.s.save()
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        # Every part gets its chance to stop, even if one before it fails
        for stop in (self.chat.shutdown, self.invoices.shutdown, self.evaluation.shutdown, self.create.shutdown,
                     self.bridge.stop if self.bridge else None):
            try:
                if stop:
                    stop()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        super().closeEvent(event)
        QApplication.instance().quit()  # also when a dialog or other window is still open
