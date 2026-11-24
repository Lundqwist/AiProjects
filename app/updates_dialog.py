"""The dialog that lists needed and available updates and applies the chosen ones."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

import wb_updates
from wb_common import run_in_background
from wb_settings import Settings


class UpdatesDialog(QDialog):
    def __init__(self, parent, settings: Settings, updates: list[wb_updates.Update]):
        super().__init__(parent)
        self.s = settings
        self.updates = updates
        self.setWindowTitle("AI Workbench: updates")
        self.resize(720, 540)
        self.boxes: list[tuple[QCheckBox, wb_updates.Update]] = []

        required = [u for u in updates if u.required]
        intro = QLabel(f"<b>{len(required)} needed</b> to work, {len(updates) - len(required)} optional."
                       if required else "Everything needed is in place. Optional updates:")
        intro.setObjectName("title")

        body = QWidget()
        lay = QVBoxLayout(body)
        for heading, group in (("Needed", required), ("Optional", [u for u in updates if not u.required])):
            if not group:
                continue
            h = QLabel(heading)
            h.setStyleSheet("color:#d4af37; font-weight:600; margin-top:8px")
            lay.addWidget(h)
            for u in group:
                box = QCheckBox(u.title)
                box.setChecked(u.required and not u.manual)
                box.setEnabled(not u.manual)
                note = u.detail + ("  (applied after a restart)" if u.restart else "")
                if u.manual:
                    note += "  You need to do this one yourself."
                detail = QLabel(note)
                detail.setObjectName("muted")
                detail.setWordWrap(True)
                detail.setContentsMargins(24, 0, 0, 6)
                row = QHBoxLayout()
                row.addWidget(box, 1)
                if u.url:
                    link = QPushButton("Open download page")
                    link.clicked.connect(lambda _=False, url=u.url: QDesktopServices.openUrl(QUrl(url)))
                    row.addWidget(link)
                lay.addLayout(row)
                lay.addWidget(detail)
                self.boxes.append((box, u))
        lay.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)

        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumHeight(140)
        self.log.hide()

        self.install = QPushButton("Install selected")
        self.install.setObjectName("primary")
        self.install.clicked.connect(self.apply)
        self.later = QPushButton("Not now")
        self.later.setToolTip("Optional updates shown now won't be shown again until a newer version comes out")
        self.later.clicked.connect(self.not_now)
        self.ask_again = QCheckBox("Check at startup")
        self.ask_again.setChecked(settings.check_updates_at_start)
        buttons = QHBoxLayout()
        buttons.addWidget(self.ask_again)
        buttons.addStretch()
        buttons.addWidget(self.later)
        buttons.addWidget(self.install)

        outer = QVBoxLayout(self)
        outer.addWidget(intro)
        outer.addWidget(scroll, 1)
        outer.addWidget(self.log)
        outer.addLayout(buttons)

    def _save_prefs(self):
        self.s.check_updates_at_start = self.ask_again.isChecked()
        try:
            self.s.save()
        except OSError:
            pass

    def not_now(self):
        for _, u in self.boxes:
            if not u.required and u.skip_key not in self.s.skipped_updates:
                self.s.skipped_updates.append(u.skip_key)
        self._save_prefs()
        self.reject()

    def apply(self):
        chosen = [u for box, u in self.boxes if box.isChecked()]
        if not chosen:
            self.not_now()
            return
        now = [u for u in chosen if not u.restart]
        later = [u for u in chosen if u.restart]
        self.install.setEnabled(False)
        self.later.setEnabled(False)
        self.log.show()

        def work(emit):
            failed = []
            for u in now:
                try:
                    wb_updates.apply_now(u, self.s, emit)
                    emit(f"✓ {u.title}")
                except Exception as e:
                    failed.append(f"{u.title}: {e}")
                    emit(f"✗ {u.title}: {e}")
            return failed

        def done(failed):
            # optional updates not chosen this time: don't ask again for these versions
            for box, u in self.boxes:
                if not box.isChecked() and not u.required and u.skip_key not in self.s.skipped_updates:
                    self.s.skipped_updates.append(u.skip_key)
            self._save_prefs()
            if failed:
                QMessageBox.warning(self, "Updates", "Some updates failed:\n\n" + "\n".join(failed))
            if later:
                names = "\n".join(f"• {u.title}" for u in later)
                if QMessageBox.question(self, "Restart to finish",
                                        f"These are applied while AI Workbench is closed:\n\n{names}\n\n"
                                        "Close and restart now? A window shows the progress.") == QMessageBox.Yes:
                    wb_updates.schedule_restart_updates(later)
                    self.done(2)  # tells the main window to quit
                    return
            self.accept()

        run_in_background(work, on_progress=self.log.appendPlainText, on_result=done,
                          on_done=lambda: self.install.setEnabled(True))
