"""Code tab: new projects, explanations, reviews and changes by the code expert, opened in VS Code."""
from __future__ import annotations

import html
import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
                               QSplitter, QTextBrowser, QVBoxLayout, QWidget)

import wb_code
from wb_common import open_path, run_in_background
from wb_settings import Settings


class CodeTab(QWidget):
    def __init__(self, settings: Settings, status):
        super().__init__()
        self.s = settings
        self.status = status
        self.changes: list[wb_code.Change] = []
        self.target: Path | None = None   # where proposed changes would be saved
        self.new_project = False
        self.busy = False

        title = QLabel("Code")
        title.setObjectName("title")
        intro = QLabel("The code expert writes new projects, explains and reviews your code, and makes changes you "
                       "can check file by file before saving. Larger jobs may go to an online AI. Everything "
                       "opens in Visual Studio Code.")
        intro.setWordWrap(True)

        self.folder = QLineEdit(self.s.code_projects_dir)
        self.folder.editingFinished.connect(self.refresh_files)
        browse = QPushButton("Folder…")
        browse.clicked.connect(self.pick_folder)
        pick_file = QPushButton("File…")
        pick_file.clicked.connect(self.pick_file)
        self.vscode_btn = QPushButton("Open in VS Code")
        self.vscode_btn.clicked.connect(self.open_vscode)
        top = QHBoxLayout()
        top.addWidget(QLabel("Project"))
        top.addWidget(self.folder, 1)
        for w in (browse, pick_file, self.vscode_btn):
            top.addWidget(w)

        self.files = QListWidget()
        self.files.setSelectionMode(QListWidget.ExtendedSelection)
        self.files.itemDoubleClicked.connect(lambda i: self.open_vscode(i.data(Qt.UserRole)))
        self.files_label = QLabel()
        self.files_label.setObjectName("muted")
        self.files_label.setWordWrap(True)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("Files (select some to work on only those)"))
        lv.addWidget(self.files, 1)
        lv.addWidget(self.files_label)

        self.request = QPlainTextEdit()
        self.request.setPlaceholderText("What should the expert do? e.g. “A Python script that renames my photos by "
                                        "the date they were taken”, or “add a --dry-run option”, or a question.")
        self.request.setMaximumHeight(90)
        self.big = QCheckBox("Larger job: may use online AI")
        self.btn_new = QPushButton("New project")
        self.btn_new.setObjectName("primary")
        self.btn_new.clicked.connect(self.ask_new_project)
        self.btn_change = QPushButton("Change / fix")
        self.btn_change.clicked.connect(self.make_change)
        self.btn_explain = QPushButton("Explain")
        self.btn_explain.clicked.connect(lambda: self.explain("explain"))
        self.btn_review = QPushButton("Review")
        self.btn_review.clicked.connect(lambda: self.explain("review"))
        actions = QHBoxLayout()
        for w in (self.btn_new, self.btn_change, self.btn_explain, self.btn_review):
            actions.addWidget(w)
        actions.addWidget(self.big)
        actions.addStretch()

        self.result = QTextBrowser()
        self.result.setOpenExternalLinks(True)
        self.change_list = QListWidget()
        self.change_list.currentRowChanged.connect(self.show_diff)
        self.diff = QTextBrowser()
        self.diff.setStyleSheet("font-family: Consolas, monospace; font-size: 9pt;")
        self.apply_btn = QPushButton("Save checked files")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self.apply_changes)
        self.discard_btn = QPushButton("Discard")
        self.discard_btn.clicked.connect(self.discard)
        changes_box = QWidget()
        cv = QVBoxLayout(changes_box)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(QLabel("Proposed files (nothing is saved until you click Save)"))
        cv.addWidget(self.change_list)
        row = QHBoxLayout()
        row.addWidget(self.apply_btn)
        row.addWidget(self.discard_btn)
        row.addStretch()
        cv.addLayout(row)
        cv.addWidget(self.diff, 2)

        right_split = QSplitter(Qt.Vertical)
        right_split.addWidget(self.result)
        right_split.addWidget(changes_box)
        right_split.setSizes([300, 400])
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(self.request)
        rv.addLayout(actions)
        rv.addWidget(right_split, 1)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([280, 820])

        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(intro)
        lay.addLayout(top)
        lay.addWidget(split, 1)
        self.show_changes()
        self.refresh_files()
        if not wb_code.vscode():
            self.vscode_btn.setEnabled(False)
            self.vscode_btn.setToolTip("Visual Studio Code was not found")

    # ---- folder and files ---------------------------------------------------------------------
    def current(self) -> Path:
        return Path(self.folder.text().strip() or self.s.code_projects_dir)

    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a project folder", str(self.current()))
        if d:
            self.folder.setText(str(Path(d)))
            self.refresh_files()

    def pick_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Choose a code file", str(self.current()))
        if f:
            self.folder.setText(str(Path(f)))
            self.refresh_files()

    def refresh_files(self):
        self.files.clear()
        root = self.current()
        if not root.exists():
            self.files_label.setText("This folder doesn't exist yet: New project creates it.")
            return
        base = root if root.is_dir() else root.parent
        files = wb_code.project_files(root)
        for p in files[:2000]:
            item = QListWidgetItem(p.relative_to(base).as_posix())
            item.setData(Qt.UserRole, str(p))
            self.files.addItem(item)
        self.files_label.setText(f"{len(files)} code files. Double-click one to open it in VS Code.")

    def selected(self) -> list[Path] | None:
        picked = [Path(i.data(Qt.UserRole)) for i in self.files.selectedItems()]
        return picked or None

    def open_vscode(self, file: str | None = None):
        try:
            root = self.current()
            wb_code.open_in_vscode(root if root.is_dir() else root.parent, file)
            self.status(f"Opened {root} in Visual Studio Code")
        except RuntimeError as e:
            QMessageBox.warning(self, "Visual Studio Code", str(e))

    # ---- work ---------------------------------------------------------------------------------
    def _begin(self, text: str) -> bool:
        if self.busy:
            return False
        self.busy = True
        for b in (self.btn_new, self.btn_change, self.btn_explain, self.btn_review):
            b.setEnabled(False)
        self.stream = ""
        self.result.setMarkdown(f"*{text}*")
        return True

    def _end(self):
        self.busy = False
        for b in (self.btn_new, self.btn_change, self.btn_explain, self.btn_review):
            b.setEnabled(True)

    def _streamed(self, piece: str):
        self.stream += piece
        # show the explanation while it comes; file contents are shown as a list afterwards
        shown = re.split(r"^=== FILE:", self.stream, maxsplit=1, flags=re.M)[0]
        self.result.setMarkdown(shown + ("\n\n*Writing files…*" if "=== FILE:" in self.stream else ""))

    def _big(self):
        return True if self.big.isChecked() else None

    def ask_new_project(self):
        desc = self.request.toPlainText().strip()
        if not desc:
            QMessageBox.information(self, "New project", "First describe the program in the box.")
            return
        guess = re.sub(r"[^\w]+", "-", " ".join(desc.split()[:4]).lower()).strip("-")[:40]
        name, ok = QInputDialog.getText(self, "New project", "Folder name for the project:", text=guess)
        if ok and name.strip():
            self.create_project(name.strip(), desc)

    def create_project(self, name: str, description: str):
        folder = Path(self.s.code_projects_dir) / re.sub(r"[^\w.-]+", "-", name).strip("-")
        if folder.exists() and any(folder.iterdir()):
            QMessageBox.warning(self, "New project", f"{folder} already has files. Pick another name, or open "
                                "it and use Change / fix.")
            return
        if not self._begin(f"The code expert is writing {folder.name}…"):
            return
        s, big = self.s, self._big()

        def work(emit):
            return wb_code.new_project(s, folder, description, big, on_text=emit)

        def done(result):
            explanation, changes, a = result
            self.target, self.new_project = folder, True
            self.show_answer(explanation, a)
            self.show_changes(changes)

        run_in_background(work, on_progress=self._streamed, on_result=done, on_error=self._failed,
                          on_done=self._end)

    def make_change(self):
        req = self.request.toPlainText().strip()
        root = self.current()
        if not req or not root.exists():
            QMessageBox.information(self, "Change / fix", "Choose a project or file and say what to change.")
            return
        if not self._begin("The code expert is working on the change…"):
            return
        s, big, only = self.s, self._big(), self.selected()

        def work(emit):
            return wb_code.change(s, root, req, only, big, on_text=emit)

        def done(result):
            explanation, changes, a = result
            self.target, self.new_project = (root if root.is_dir() else root.parent), False
            self.show_answer(explanation, a)
            self.show_changes(changes)

        run_in_background(work, on_progress=self._streamed, on_result=done, on_error=self._failed,
                          on_done=self._end)

    def explain(self, mode: str):
        root = self.current()
        if not root.exists():
            QMessageBox.information(self, "Explain", "Choose a project folder or file first.")
            return
        if not self._begin("The code expert is reading the code…"):
            return
        s, big, only, q = self.s, self._big(), self.selected(), self.request.toPlainText().strip()

        def work(emit):
            return wb_code.explain(s, root, mode, q, only, big, on_text=emit)

        run_in_background(work, on_progress=self._streamed, on_result=lambda a: self.show_answer(a.text, a),
                          on_error=self._failed, on_done=self._end)

    def _failed(self, msg: str):
        self.result.setMarkdown(f"**Error:** {msg}\n\nIs Ollama running, and is the code expert's model installed?")

    def show_answer(self, text: str, a):
        self.result.setMarkdown(f"{text}\n\n---\n> " + a.credit().replace("\n", "  \n> "))

    # ---- proposed changes ---------------------------------------------------------------------
    def show_changes(self, changes: list | None = None):
        self.changes = changes or []
        self.change_list.clear()
        for c in self.changes:
            item = QListWidgetItem(f"{'NEW' if c.is_new else 'CHANGED'}  {c.path}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.change_list.addItem(item)
        self.apply_btn.setEnabled(bool(self.changes))
        self.discard_btn.setEnabled(bool(self.changes))
        self.apply_btn.setText("Save files and open in VS Code" if self.new_project else "Save checked files")
        if self.changes:
            self.change_list.setCurrentRow(0)
        else:
            self.diff.setHtml("<p style='color:#9fb0cc'>No file changes proposed.</p>")

    def show_diff(self, row: int):
        if not 0 <= row < len(self.changes):
            return
        lines = []
        for line in self.changes[row].diff().splitlines():
            color = "#9be59b" if line.startswith("+") else "#ff8a80" if line.startswith("-") else \
                "#d4af37" if line.startswith("@@") else "#eef2f8"
            lines.append(f"<span style='color:{color}'>{html.escape(line) or '&nbsp;'}</span>")
        self.diff.setHtml("<pre style='margin:0'>" + "\n".join(lines) + "</pre>")

    def apply_changes(self):
        picked = [c for i, c in enumerate(self.changes) if self.change_list.item(i).checkState() == Qt.Checked]
        if not picked or not self.target:
            return
        done = wb_code.apply(self.target, picked)
        self.status(f"Saved {len(done)} file(s) in {self.target}")
        replaced = sum(not c.is_new for c in picked)
        self.result.append(f"<p style='color:#9be59b'>Saved {len(done)} file(s) in {html.escape(str(self.target))}"
                           + (f"; the {replaced} replaced file(s) were copied to .workbench-history first"
                              if replaced else "") + ".</p>")
        self.folder.setText(str(self.target))
        if self.new_project:
            self.open_vscode()
        self.new_project = False
        self.show_changes([])
        self.refresh_files()

    def discard(self):
        self.show_changes([])
