"""Experts tab: ask anything; the workbench picks a local expert or an online AI and says who answered."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QTextBrowser, QVBoxLayout, QWidget)

import wb_ai
from wb_common import run_in_background
from wb_settings import Settings

SYSTEM = ("You are a helpful expert. Answer correctly and clearly; use Markdown, with code in fenced blocks. "
          "Say so when you are unsure or don't know, instead of guessing.")


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

        self.online_label = QLabel()
        self.online_label.setObjectName("muted")
        self.online_label.setWordWrap(True)
        self.online_label.setTextFormat(Qt.RichText)

        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(intro)
        lay.addLayout(top)
        lay.addWidget(self.view, 1)
        lay.addLayout(row)
        lay.addWidget(self.online_label)
        self.apply_settings()
        self.render()

    def apply_settings(self):
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
        history = [{"role": "system", "content": SYSTEM}]
        for t in self.turns:
            if t["a"]:
                history += [{"role": "user", "content": t["q"]}, {"role": "assistant", "content": t["a"]}]
        history.append({"role": "user", "content": q})
        entry = {"q": q, "a": "", "credit": "", "busy": True}
        self.turns.append(entry)
        self.render()
        s = self.s
        big = True if self.big.isChecked() else None

        def work(emit):
            return wb_ai.ask(s, history, expert=choice if choice in wb_ai.EXPERTS else None,
                             online_first=choice == "online", big=big, on_text=emit)

        def progress(piece):
            entry["a"] += piece
            self.render()

        def done(a: wb_ai.Answer):
            entry["a"], entry["credit"] = a.text, a.credit()
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
        self.render()

    def render(self):
        if not self.turns:
            self.view.setMarkdown("*Ask a question below. Try “Why does my C# async method deadlock?”, "
                                  "“How many weekdays are there in March 2027?” or “Write a short cover letter "
                                  "for a remote junior .NET job”.*")
            return
        md = []
        for t in self.turns:
            md.append(f"**You:** {t['q']}\n")
            md.append(t["a"] or ("*Thinking…*" if t["busy"] else ""))
            if t["credit"]:
                md.append("\n> " + t["credit"].replace("\n", "  \n> "))
            md.append("\n\n---\n")
        self.view.setMarkdown("\n".join(md))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())
