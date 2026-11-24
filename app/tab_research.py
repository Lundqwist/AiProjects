"""Research tab: ask a question, watch the agent's steps, read and check the cited report."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QSplitter, QTextBrowser, QVBoxLayout,
                               QWidget)

import agent
import check_report
import wb_ai
from wb_common import open_path, run_in_background
from wb_settings import Settings


class ResearchTab(QWidget):
    def __init__(self, settings: Settings, status):
        super().__init__()
        self.s = settings
        self.status = status

        self.question = QLineEdit()
        self.question.setPlaceholderText("What should the agent research? e.g. Which remote .NET jobs in Europe ask for Azure?")
        self.question.returnPressed.connect(self.start)
        self.go = QPushButton("Research")
        self.go.setObjectName("primary")
        self.go.clicked.connect(self.start)
        ask = QHBoxLayout()
        ask.addWidget(self.question, 1)
        ask.addWidget(self.go)

        self.steps = QPlainTextEdit(readOnly=True)
        self.steps.setMaximumHeight(130)
        self.report = QTextBrowser()
        self.report.setOpenExternalLinks(True)
        self.check_label = QLabel("")
        self.check_label.setWordWrap(True)

        self.history = QListWidget()
        self.history.currentItemChanged.connect(self.show_saved)
        open_folder = QPushButton("Open reports folder")
        open_folder.clicked.connect(lambda: open_path(self.s.reports_dir))
        left = QWidget()
        l = QVBoxLayout(left)
        l.setContentsMargins(0, 0, 0, 0)
        l.addWidget(QLabel("Earlier reports"))
        l.addWidget(self.history, 1)
        l.addWidget(open_folder)

        right = QWidget()
        r = QVBoxLayout(right)
        r.setContentsMargins(0, 0, 0, 0)
        r.addWidget(QLabel("Agent steps"))
        r.addWidget(self.steps)
        r.addWidget(self.check_label)
        r.addWidget(self.report, 1)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([260, 740])

        title = QLabel("Research")
        title.setObjectName("title")
        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(QLabel("The agent searches the web, reads pages and writes a report with "
                             "numbered sources. Facts it didn't read are flagged below the steps."))
        lay.addLayout(ask)
        lay.addWidget(split, 1)
        self.load_history()

    def start(self):
        q = self.question.text().strip()
        if not q:
            return
        self.go.setEnabled(False)
        self.steps.clear()
        self.report.clear()
        self.check_label.setText("")
        self.steps.appendPlainText(f"Researching with {self.s.agent_model}…")
        out_dir = Path(self.s.reports_dir)
        s = self.s

        def writer(messages):
            """A full report is a larger job: an online AI writes it while it has tokens."""
            a = wb_ai.ask(s, messages, task="report", local_ok=False, temperature=0.2)
            if a is None:
                return None
            if a.text:
                return a.text, a.credit()
            return "", "Online AI not used: " + "; ".join(a.notes)

        def work(emit):
            _, path = agent.run(q, on_step=emit, out_dir=out_dir, writer=writer)
            return path

        def done(path: Path):
            self.steps.appendPlainText(f"Done. Saved to {path}")
            self.load_history(select=path)

        run_in_background(work, on_progress=self.steps.appendPlainText, on_result=done,
                          on_done=lambda: self.go.setEnabled(True))

    def load_history(self, select: Path | None = None):
        self.history.clear()
        folder = Path(self.s.reports_dir)
        reports = sorted(folder.glob("*.md"), reverse=True) if folder.exists() else []
        for p in reports:
            first = p.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip()
            item = QListWidgetItem(f"{p.stem[:8]} {p.stem[9:11]}:{p.stem[11:13]}  {first}")
            item.setData(Qt.UserRole, str(p))
            item.setToolTip(first)
            self.history.addItem(item)
            if select and p == select:
                self.history.setCurrentItem(item)

    def show_saved(self, item):
        if not item:
            return
        path = Path(item.data(Qt.UserRole))
        self.report.setMarkdown(path.read_text(encoding="utf-8"))
        try:
            total, missing = check_report.check(path)
        except (OSError, ValueError):
            self.check_label.setText("")
            return
        if missing:
            self.check_label.setText(f"<span style='color:#ff8a80'><b>Check:</b> {len(missing)} of {total} "
                                     f"numbers/IDs were not in any page the agent read: "
                                     f"{', '.join(missing[:8])}</span>")
        else:
            self.check_label.setText(f"<span style='color:#9be59b'><b>Check:</b> all {total} numbers, "
                                     f"versions and names appear in the pages the agent read.</span>")
