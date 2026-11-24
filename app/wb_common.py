"""Shared helpers: background workers, folder pickers, small widgets, theme."""
from __future__ import annotations

import os
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLineEdit, QMessageBox, QPushButton,
                               QWidget)


class WorkerSignals(QObject):
    progress = Signal(object)
    result = Signal(object)
    error = Signal(str)
    done = Signal()


class Worker(QRunnable):
    """Run fn(*args, emit=..., **kwargs) on a pool thread. emit(x) sends progress to the GUI."""

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = WorkerSignals()

    def run(self):
        try:
            result = self.fn(*self.args, emit=self.signals.progress.emit, **self.kwargs)
        except Exception as e:
            traceback.print_exc()
            self.signals.error.emit(f"{type(e).__name__}: {e}")
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.done.emit()


_running: set[Worker] = set()  # strong references: otherwise Python may collect a worker (and its
                               # signals) before its result reaches the GUI thread


def run_in_background(fn, *args, on_progress=None, on_result=None, on_error=None, on_done=None,
                      pool: QThreadPool | None = None, **kwargs) -> Worker:
    w = Worker(fn, *args, **kwargs)
    w.setAutoDelete(False)
    _running.add(w)
    w.signals.done.connect(lambda: _running.discard(w))
    if on_progress:
        w.signals.progress.connect(on_progress)
    if on_result:
        w.signals.result.connect(on_result)
    w.signals.error.connect(on_error or (lambda msg: QMessageBox.warning(None, "AI Workbench", msg)))
    if on_done:
        w.signals.done.connect(on_done)
    (pool or QThreadPool.globalInstance()).start(w)
    return w


def open_path(path: str | Path) -> None:
    """Open a file or folder in its Windows default program."""
    p = Path(path)
    if p.exists():
        os.startfile(str(p))  # noqa: S606 - local, user-chosen path


class PathPicker(QWidget):
    """A text box with a Browse button for a folder."""

    def __init__(self, value: str = "", caption: str = "Choose folder"):
        super().__init__()
        self.edit = QLineEdit(value)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        open_btn = QPushButton("Open")
        open_btn.clicked.connect(lambda: open_path(self.edit.text()))
        self.caption = caption
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.edit, 1)
        lay.addWidget(browse)
        lay.addWidget(open_btn)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, self.caption, self.edit.text())
        if d:
            self.edit.setText(str(Path(d)))

    def text(self) -> str:
        return self.edit.text().strip()


NAVY, NAVY_2, NAVY_3, GOLD, TEXT, MUTED = "#0b1a33", "#13284d", "#1d3a6b", "#d4af37", "#eef2f8", "#9fb0cc"

THEME = f"""
QWidget {{ background: {NAVY}; color: {TEXT}; font-family: 'Segoe UI'; font-size: 10pt; }}
QTabWidget::pane {{ border: 1px solid {NAVY_3}; top: -1px; }}
QTabBar::tab {{ background: {NAVY_2}; color: {MUTED}; padding: 8px 18px; border: 1px solid {NAVY_3};
               border-bottom: none; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {NAVY}; color: {GOLD}; font-weight: 600; }}
QPushButton {{ background: {NAVY_2}; border: 1px solid {NAVY_3}; padding: 6px 14px; border-radius: 4px; }}
QPushButton:hover {{ border-color: {GOLD}; color: {GOLD}; }}
QPushButton:pressed, QPushButton:checked {{ background: {GOLD}; color: {NAVY}; }}
QPushButton:disabled {{ color: #5a6b88; border-color: {NAVY_2}; }}
QPushButton#primary {{ background: {GOLD}; color: {NAVY}; font-weight: 600; border: none; }}
QPushButton#primary:hover {{ background: #e6c45a; color: {NAVY}; }}
QPushButton#primary:disabled {{ background: #6b5a24; color: {NAVY_2}; }}
QLineEdit, QTextEdit, QTextBrowser, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget,
QTableWidget {{ background: {NAVY_2}; border: 1px solid {NAVY_3}; border-radius: 4px; padding: 4px;
               selection-background-color: {GOLD}; selection-color: {NAVY}; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {GOLD}; }}
QHeaderView::section {{ background: {NAVY_3}; color: {TEXT}; border: none; padding: 4px; }}
QGroupBox {{ border: 1px solid {NAVY_3}; border-radius: 6px; margin-top: 14px; padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; color: {GOLD}; font-weight: 600; }}
QProgressBar {{ background: {NAVY_2}; border: 1px solid {NAVY_3}; border-radius: 4px; text-align: center; }}
QProgressBar::chunk {{ background: {GOLD}; border-radius: 3px; }}
QSlider::groove:horizontal {{ height: 4px; background: {NAVY_3}; }}
QSlider::handle:horizontal {{ background: {GOLD}; width: 14px; margin: -6px 0; border-radius: 7px; }}
QStatusBar {{ background: {NAVY_2}; color: {MUTED}; }}
QLabel#title {{ color: {GOLD}; font-size: 14pt; font-weight: 600; }}
QLabel#muted {{ color: {MUTED}; }}
QCheckBox::indicator:checked {{ background: {GOLD}; border: 1px solid {GOLD}; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {NAVY_3}; background: {NAVY_2}; }}
QScrollArea {{ border: none; }}
"""
