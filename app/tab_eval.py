"""Evaluation panel: run the promptfoo test suites and show pass/fail per test and model."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from wb_settings import Settings

ANSI = re.compile(r"\x1b\[[0-9;]*m")
SUITES = [("rag.yaml", "Documents (RAG)"), ("invoices.yaml", "Invoices")]


class EvalTab(QWidget):
    def __init__(self, settings: Settings, status):
        super().__init__()
        self.s = settings
        self.status = status
        self.proc: QProcess | None = None
        self.export_file = Path(tempfile.gettempdir()) / "ai-workbench-eval.json"

        title = QLabel("Evaluation")
        title.setObjectName("title")
        info = QLabel("Runs the automated test suites against the real project code with your models. "
                      "Use it after changing a model or a setting. The documents suite takes about 3 minutes.")
        info.setWordWrap(True)

        self.buttons = []
        row = QHBoxLayout()
        for file, label in SUITES:
            b = QPushButton(f"Test {label}")
            b.setObjectName("primary")
            b.clicked.connect(lambda _=False, f=file: self.run_suite(f))
            self.buttons.append(b)
            row.addWidget(b)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(lambda: self.proc and self.proc.kill())
        viewer = QPushButton("Open promptfoo web viewer")
        viewer.clicked.connect(self.open_viewer)
        row.addWidget(self.stop_btn)
        row.addWidget(viewer)
        row.addStretch()

        self.summary = QLabel("")
        self.summary.setObjectName("title")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Result", "Model", "Test", "Why it failed / answer"])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Interactive)
        h.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(2, 280)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(True)
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumBlockCount(2000)

        split = QSplitter(Qt.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.log)
        split.setSizes([450, 200])

        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(info)
        lay.addLayout(row)
        lay.addWidget(self.summary)
        lay.addWidget(split, 1)

    def _process(self) -> QProcess:
        p = QProcess(self)
        p.setWorkingDirectory(self.s.eval_dir)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PROMPTFOO_DISABLE_TELEMETRY", "1")
        env.insert("PROMPTFOO_DISABLE_UPDATE", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("OLLAMA_HOST", self.s.ollama_host)
        p.setProcessEnvironment(env)
        p.setProcessChannelMode(QProcess.MergedChannels)
        return p

    def run_suite(self, file: str):
        if not Path(self.s.promptfoo_path).exists():
            QMessageBox.warning(self, "Evaluation", f"promptfoo not found at {self.s.promptfoo_path}.\n"
                                "Install it with: npm install -g promptfoo, or set its path in Settings.")
            return
        for b in self.buttons:
            b.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.table.setRowCount(0)
        self.log.clear()
        self.summary.setText(f"Running {file}…")
        self.proc = self._process()
        self.proc.readyRead.connect(lambda: self.log.appendPlainText(
            ANSI.sub("", bytes(self.proc.readAll()).decode("utf-8", "replace")).rstrip()))
        self.proc.finished.connect(lambda code, _status: self.finished(file))
        # cmd /c is needed to start an npm .cmd shim
        self.proc.start("cmd.exe", ["/c", self.s.promptfoo_path, "eval", "-c", file, "-j", "1",
                                    "--no-table", "--no-cache"])

    def finished(self, file: str):
        exporter = self._process()
        exporter.start("cmd.exe", ["/c", self.s.promptfoo_path, "export", "eval", "latest",
                                   "-o", str(self.export_file)])
        exporter.waitForFinished(60000)
        for b in self.buttons:
            b.setEnabled(True)
        self.stop_btn.setEnabled(False)
        try:
            data = json.loads(self.export_file.read_text(encoding="utf-8"))
            self.show_results(data)
        except (OSError, ValueError) as e:
            self.summary.setText(f"{file}: no results ({e})")

    def show_results(self, data: dict):
        results = data.get("results", {}).get("results", [])
        passed = sum(1 for r in results if r.get("success"))
        self.summary.setText(f"{passed} of {len(results)} passed "
                             f"({100 * passed / max(len(results), 1):.0f} %)")
        results = sorted(results, key=lambda r: r.get("success", False))  # failures first
        self.table.setRowCount(len(results))
        for i, r in enumerate(results):
            ok = r.get("success", False)
            vars_ = r.get("vars", {})
            test = vars_.get("question") or vars_.get("file") or json.dumps(vars_)
            if ok:
                detail = (r.get("response") or {}).get("output", "")
            else:
                reasons = [c.get("reason", "") for c in (r.get("gradingResult") or {}).get("componentResults", [])
                           if not c.get("pass")]
                detail = " | ".join(reasons) or r.get("error", "")
            cells = ["PASS" if ok else "FAIL", (r.get("provider") or {}).get("label", ""), test,
                     str(detail).replace("\n", " ")[:400]]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 0:
                    item.setForeground(QColor("#9be59b" if ok else "#ff8a80"))
                self.table.setItem(i, c, item)
        self.table.resizeRowsToContents()

    def open_viewer(self):
        # Starts promptfoo's local web viewer in its own console window, which opens the browser
        QProcess.startDetached("cmd.exe", ["/c", "start", "promptfoo viewer", self.s.promptfoo_path, "view", "-y"],
                               self.s.eval_dir)

    def shutdown(self):
        if self.proc and self.proc.state() != QProcess.NotRunning:
            self.proc.kill()
