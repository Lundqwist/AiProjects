"""Experts tab: ask anything; the workbench picks a local expert or an online AI and says who answered."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget)

import wb_ai
import wb_brain
import wb_code
import wb_trace
from wb_common import run_in_background
from wb_settings import Settings

SYSTEM = ("You are a helpful expert. Answer correctly and clearly; use Markdown, with code in fenced blocks. "
          "Say so when you are unsure or don't know, instead of guessing.")
FILES_SYSTEM = """
You CAN create files. When the user asks for a website, a program, a script or any files, write every file
COMPLETE in exactly this form, one after another:

=== FILE: project-name/relative/path.ext ===
```language
the whole file
```

Start every path with one short folder name for the project (e.g. cat-website/index.html,
cat-website/style.css). Whole files only, never fragments or "...". Before the files, say in short what you
made and how to open or run it. The workbench shows the files to the user, who saves them into their folder
({folder}) with one click. Never say you can't create files."""


class ExpertsTab(QWidget):
    def __init__(self, settings: Settings, status):
        super().__init__()
        self.s = settings
        self.status = status
        self.turns: list[dict] = []   # {"q", "a", "credit", "busy"}
        self.busy = False

        title = QLabel("Experts")
        title.setObjectName("title")
        intro = QLabel("Each question goes to the local expert that fits it: code, reasoning, writing or general. "
                       "Larger jobs go to an online AI while it has tokens, then back to this PC. "
                       "Every answer says who wrote it, where it ran and why.")
        intro.setWordWrap(True)

        self.who = QComboBox()
        self.who.addItem("Auto: pick the right expert", None)
        for key, e in wb_ai.EXPERTS.items():
            self.who.addItem(f"{e.name}: {e.about}", key)
        self.who.addItem("Online AI first (falls back to this PC)", "online")
        self.big = QCheckBox("Larger job: may use online AI")
        self.big.setToolTip("Short questions stay on this PC. Tick this to let an online AI take this one.")
        top = QHBoxLayout()
        top.addWidget(QLabel("Who answers"))
        top.addWidget(self.who, 1)
        top.addWidget(self.big)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.question = QPlainTextEdit()
        self.question.setPlaceholderText("Ask anything, or paste code, an error or a text to work on. "
                                         "Ctrl+Enter sends.")
        self.question.setMaximumHeight(110)
        QShortcut(QKeySequence("Ctrl+Return"), self.question, activated=self.ask)
        self.ask_btn = QPushButton("Ask")
        self.ask_btn.setObjectName("primary")
        self.ask_btn.clicked.connect(self.ask)
        clear = QPushButton("New chat")
        clear.clicked.connect(self.clear)
        buttons = QVBoxLayout()
        buttons.addWidget(self.ask_btn)
        buttons.addWidget(clear)
        buttons.addStretch()
        row = QHBoxLayout()
        row.addWidget(self.question, 1)
        row.addLayout(buttons)

        # Files the experts write are saved here (the same work folder as the Code tab and voice)
        self.folder = QLineEdit(self.s.code_projects_dir)
        self.folder.setToolTip("Websites, programs and other files the experts write are saved in this folder")
        self.folder.editingFinished.connect(self._folder_changed)
        browse = QPushButton("Folder…")
        browse.clicked.connect(self.pick_folder)
        self.save_btn = QPushButton("Save files")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self.save_files)
        self.files_label = QLabel()
        self.files_label.setObjectName("muted")
        self.files_label.setWordWrap(True)
        files_row = QHBoxLayout()
        files_row.addWidget(QLabel("Save files to"))
        files_row.addWidget(self.folder, 1)
        files_row.addWidget(browse)
        files_row.addWidget(self.save_btn)
        self.pending: list[wb_code.Change] = []   # files in the last answer, not saved yet

        self.online_label = QLabel()
        self.online_label.setObjectName("muted")
        self.online_label.setWordWrap(True)
        self.online_label.setTextFormat(Qt.RichText)

        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(intro)
        lay.addLayout(top)
        lay.addWidget(self.view, 1)
        lay.addLayout(files_row)
        lay.addWidget(self.files_label)
        lay.addLayout(row)
        lay.addWidget(self.online_label)
        self.show_pending([])
        self.apply_settings()
        self.render()

    def apply_settings(self):
        if hasattr(self, "folder") and self.folder.text().strip() != self.s.code_projects_dir:
            self.folder.setText(self.s.code_projects_dir)   # changed in Settings
        mode = self.s.online_mode
        self.big.setEnabled(mode == "big")
        self.big.setChecked(mode == "always")
        self.refresh_online()

    def refresh_online(self):
        if self.s.online_mode == "off":
            self.online_label.setText("Online AI: turned off in Settings. Everything is answered on this PC.")
            return
        lines = []
        for p in self.s.online_providers or []:
            ok, state = wb_ai.provider_state(p)
            dot = "#9be59b" if ok else "#ff8a80"
            lines.append(f"<span style='color:{dot}'>●</span> {p['name']} ({p.get('model', '')}): {state}")
        mode = ("used for every question" if self.s.online_mode == "always"
                else f"used for larger jobs (ticked, or over {self.s.online_min_chars:,} characters)".replace(",", " "))
        self.online_label.setText(f"Online AI {mode}.<br>" + ("<br>".join(lines) or "No online AI set up yet: "
                                  "add one in Settings → Experts and online AI."))

    def ask(self):
        q = self.question.toPlainText().strip()
        if not q or self.busy:
            return
        self.question.clear()
        self.busy = True
        self.ask_btn.setEnabled(False)
        choice = self.who.currentData()
        history = [{"role": "system", "content": SYSTEM + FILES_SYSTEM.format(folder=self.target())}]
        for t in self.turns:
            if t["a"]:
                history += [{"role": "user", "content": t["q"]}, {"role": "assistant", "content": t["a"]}]
        history.append({"role": "user", "content": q})
        entry = {"q": q, "a": "", "credit": "", "busy": True}
        self.turns.append(entry)
        self.render()
        s = self.s
        big = True if self.big.isChecked() else None

        last = next((t["a"] for t in reversed(self.turns[:-1]) if t["a"]), "")

        def work(emit):
            with wb_trace.run(f"Experts: {q}", "experts"):
                return answer(emit)

        def answer(emit):
            notes = []
            if s.brain_in_experts:
                wb_brain.configure(s)
                try:
                    wb_brain.note_correction(q, last, "experts")
                except OSError:
                    pass
                # Your notes stay on this PC: they are only used when a local expert answers
                online = bool(wb_ai.online_ready(s)) and (
                    s.online_mode != "off" if choice == "online" else wb_ai.wants_online(s, history, "chat", big)[0])
                if online and not s.online_for_documents:
                    notes.append("Your brain was not used: this question went online, and your notes stay on this PC")
                else:
                    hits = wb_brain.relevant(q, k=4)
                    extra = wb_brain.context() + wb_brain.facts_block(hits)
                    if extra:
                        history[0]["content"] += "\n" + extra
                        used = (["your rules"] if wb_brain.rules() else []) + \
                            list(dict.fromkeys(h.label for h in hits))
                        notes.append("Used from your brain: " + ", ".join(used))
            a = wb_ai.ask(s, history, expert=choice if choice in wb_ai.EXPERTS else None,
                          online_first=choice == "online", big=big, on_text=emit)
            if a:
                a.notes = notes + a.notes
            return a

        def progress(piece):
            entry["a"] += piece
            self.render()

        target = self.target()

        def done(a: wb_ai.Answer):
            entry["a"], entry["credit"] = a.text, a.credit()
            explanation, changes = wb_code.parse(a.text, target)
            if changes:
                entry["shown"] = explanation   # the files are listed below the chat, not printed in it
                self.show_pending(changes)
            self.status(f"Answered by {a.by}")

        def failed(msg):
            entry["a"] = f"Error: {msg}"
            entry["credit"] = "Nobody could answer: is Ollama running, and is the expert's model installed?"

        def finished():
            entry["busy"] = False
            self.busy = False
            self.ask_btn.setEnabled(True)
            self.render()
            self.refresh_online()

        run_in_background(work, on_progress=progress, on_result=done, on_error=failed, on_done=finished)

    def clear(self):
        self.turns.clear()
        self.show_pending([])
        self.render()

    # ---- files the experts write --------------------------------------------------------------
    def target(self) -> Path:
        return Path(self.folder.text().strip() or self.s.code_projects_dir)

    def _folder_changed(self):
        path = self.folder.text().strip()
        if path and path != self.s.code_projects_dir:
            self.s.code_projects_dir = path   # one work folder for Experts, the Code tab and voice
            self.s.save()
            wb_code.configure(self.s)
        if self.pending:  # same answer, new place: see again what is new and what would be replaced
            last = next((t["a"] for t in reversed(self.turns) if t.get("shown") is not None), "")
            self.show_pending(wb_code.parse(last, self.target())[1])

    def pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Folder for the files the experts write", str(self.target()))
        if d:
            self.folder.setText(str(Path(d)))
            self._folder_changed()

    def show_pending(self, changes: list):
        self.pending = changes
        self.save_btn.setEnabled(bool(changes))
        if not changes:
            self.save_btn.setText("Save files")
            self.files_label.setText("Ask for a website, a program or any files, and they can be saved here "
                                     "with one click.")
            return
        replaced = [c.path for c in changes if not c.is_new]
        self.save_btn.setText(f"Save {len(changes)} file{'s' if len(changes) != 1 else ''}")
        self.files_label.setText("Not saved yet: " + ", ".join(
            f"{c.path}{' (replaces the old one)' if not c.is_new else ''}" for c in changes)
            + (". Replaced files are copied to .workbench-history first." if replaced else "."))

    def save_files(self):
        if not self.pending:
            return
        folder = self.target()
        try:
            done = wb_code.apply(folder, self.pending)
        except OSError as e:
            QMessageBox.warning(self, "Save files", f"Couldn't save in {folder}: {e}")
            return
        tops = sorted({p.split("/")[0] for p in done})
        opened = folder / tops[0] if len(tops) == 1 and (folder / tops[0]).is_dir() else folder
        self.status(f"Saved {len(done)} file(s) in {opened}")
        self.show_pending([])
        self.files_label.setText(f"Saved {len(done)} file(s) in {opened}.")
        try:
            wb_code.open_in_vscode(opened)
        except RuntimeError:
            os.startfile(opened)  # noqa: S606 - the user's own folder

    def render(self):
        if not self.turns:
            self.view.setMarkdown("*Ask a question below. Try “Why does my C# async method deadlock?”, "
                                  "“How many weekdays are there in March 2027?” or “Write a short cover letter "
                                  "for a remote junior .NET job”.*")
            return
        md = []
        for t in self.turns:
            md.append(f"**You:** {t['q']}\n")
            md.append(t.get("shown") or t["a"] or ("*Thinking…*" if t["busy"] else ""))
            if t["credit"]:
                md.append("\n> " + t["credit"].replace("\n", "  \n> "))
            md.append("\n\n---\n")
        self.view.setMarkdown("\n".join(md))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())
