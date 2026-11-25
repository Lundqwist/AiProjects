"""Documents panel: manage the document database and ask questions with cited answers."""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QSplitter, QTextBrowser,
                               QVBoxLayout, QWidget)

import rag
import wb_ai
from wb_common import run_in_background
from wb_settings import Settings

SUFFIXES = {".pdf", ".md", ".txt"}


class DocumentsTab(QWidget):
    def __init__(self, settings: Settings, status):
        super().__init__()
        self.s = settings
        self.status = status
        self.chat: list[dict] = []
        self.busy = False
        self.setAcceptDrops(True)

        # Left: the database
        self.sources = QListWidget()
        self.sources.setSelectionMode(QListWidget.ExtendedSelection)
        add_files = QPushButton("Add files…")
        add_files.clicked.connect(self.add_files)
        add_folder = QPushButton("Add folder…")
        add_folder.clicked.connect(self.add_folder)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self.remove_selected)
        self.db_label = QLabel()
        self.db_label.setObjectName("muted")
        self.db_label.setWordWrap(True)
        left = QWidget()
        l = QVBoxLayout(left)
        title = QLabel("Documents")
        title.setObjectName("title")
        l.addWidget(title)
        l.addWidget(QLabel("PDF, Markdown and text files. Drag files here too."))
        l.addWidget(self.sources, 1)
        row = QHBoxLayout()
        row.addWidget(add_files)
        row.addWidget(add_folder)
        l.addLayout(row)
        l.addWidget(remove)
        l.addWidget(self.db_label)

        # Right: the chat
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.question = QLineEdit()
        self.question.setPlaceholderText("Ask about your documents, in English or Swedish…")
        self.question.returnPressed.connect(self.ask)
        self.ask_btn = QPushButton("Ask")
        self.ask_btn.setObjectName("primary")
        self.ask_btn.clicked.connect(self.ask)
        clear = QPushButton("New chat")
        clear.clicked.connect(self.clear_chat)
        right = QWidget()
        r = QVBoxLayout(right)
        r.addWidget(self.view, 1)
        ask_row = QHBoxLayout()
        ask_row.addWidget(self.question, 1)
        ask_row.addWidget(self.ask_btn)
        ask_row.addWidget(clear)
        r.addLayout(ask_row)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([300, 700])
        QVBoxLayout(self).addWidget(split)
        self.refresh_sources()
        self.render()

    # ---- database -----------------------------------------------------------------------
    def refresh_sources(self):
        def load(emit):
            return rag.list_sources()

        def show(sources: dict):
            self.sources.clear()
            for name, n in sources.items():
                item = QListWidgetItem(f"{name}   ({n} chunks)")
                item.setData(Qt.UserRole, name)
                self.sources.addItem(item)
            self.db_label.setText(f"{len(sources)} files, {sum(sources.values())} chunks\n{self.s.rag_db}")

        run_in_background(load, on_result=show)

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add documents", "",
                                                "Documents (*.pdf *.md *.txt)")
        self.ingest([Path(f) for f in files])

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Add every document in a folder")
        if d:
            self.ingest(sorted(p for p in Path(d).rglob("*") if p.suffix.lower() in SUFFIXES))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = []
        for url in e.mimeData().urls():
            p = Path(url.toLocalFile())
            paths += sorted(q for q in p.rglob("*") if q.suffix.lower() in SUFFIXES) if p.is_dir() else [p]
        self.ingest([p for p in paths if p.suffix.lower() in SUFFIXES])

    def ingest(self, files: list[Path]):
        if not files:
            return

        def work(emit):
            done, failed = 0, []
            for i, f in enumerate(files, 1):
                emit(f"Adding {f.name} ({i}/{len(files)})…")
                try:
                    rag.ingest_file(f, f.parent)
                    done += 1
                except Exception as ex:
                    failed.append(f"{f.name}: {ex}")
            return done, failed

        def finished(result):
            done, failed = result
            self.status(f"Added {done} file(s)" + (f", {len(failed)} failed" if failed else ""))
            if failed:
                QMessageBox.warning(self, "Some files were skipped", "\n".join(failed[:20]))
            self.refresh_sources()

        run_in_background(work, on_progress=self.status, on_result=finished)

    def remove_selected(self):
        names = [i.data(Qt.UserRole) for i in self.sources.selectedItems()]
        if not names:
            return
        if QMessageBox.question(self, "Remove documents",
                                f"Remove {len(names)} document(s) from the database?\n"
                                "The files themselves are not touched.") != QMessageBox.Yes:
            return
        for n in names:
            rag.delete_source(n)
        self.refresh_sources()

    # ---- chat ---------------------------------------------------------------------------
    def ask(self):
        q = self.question.text().strip()
        if not q or self.busy:
            return
        self.busy = True
        self.ask_btn.setEnabled(False)
        self.question.clear()
        entry = {"q": q, "a": "", "sources": [], "thinking": True}
        self.chat.append(entry)
        self.render()
        temperature = self.s.temperature
        s = self.s

        def work(emit):
            hits = rag.search(q)
            emit(("sources", [h.label for h in hits]))
            if not hits:
                for part in rag.answer_stream(q, hits):
                    emit(("text", part))
                emit(("credit", "Nothing was searched: the document database is empty."))
                return
            # Your documents stay on this PC unless Settings allows online AI for them
            a = wb_ai.ask(s, rag.build_messages(q, hits), task="documents", expert="general",
                          big=None if s.online_for_documents else False, temperature=temperature,
                          on_text=lambda p: emit(("text", p)) if p else None)
            files = len({h.source for h in hits})
            emit(("credit", f"{a.credit().replace(' · picked because you chose it', '')}\n"
                            f"– from {len(hits)} passages in {files} of your documents (listed below)"
                            + ("" if a.online else "; nothing was sent online")))

        def progress(item):
            kind, value = item
            if kind == "sources":
                entry["sources"] = value
            elif kind == "credit":
                entry["credit"] = value
            else:
                entry["thinking"] = False
                entry["a"] += value
            self.render()

        def done():
            entry["thinking"] = False
            self.busy = False
            self.ask_btn.setEnabled(True)
            self.render()

        def failed(msg):
            entry["a"] = f"Error: {msg}"

        run_in_background(work, on_progress=progress, on_error=failed, on_done=done)

    def clear_chat(self):
        self.chat.clear()
        self.render()

    def render(self):
        if not self.chat:
            self.view.setHtml("<p style='color:#9fb0cc'>Add documents on the left, then ask a question. "
                              "Answers cite their sources as [1], [2] and say so when the answer "
                              "isn't in your documents.</p>")
            return
        parts = []
        for e in self.chat:
            parts.append(f"<p style='color:#d4af37'><b>You:</b> {html.escape(e['q'])}</p>")
            answer = html.escape(e["a"]).replace("\n", "<br>") or ("<i>Thinking…</i>" if e["thinking"] else "")
            parts.append(f"<p>{answer}</p>")
            if e.get("credit"):
                parts.append(f"<p style='color:#d4af37; font-size:9pt'>"
                             f"{html.escape(e['credit']).replace(chr(10), '<br>')}</p>")
            if e["sources"]:
                items = "".join(f"<li>[{i}] {html.escape(s)}</li>" for i, s in enumerate(e["sources"], 1))
                parts.append(f"<ul style='color:#9fb0cc; font-size:9pt'>{items}</ul>")
            parts.append("<hr>")
        self.view.setHtml("".join(parts))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())
