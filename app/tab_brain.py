"""Brain tab: the assistant's long-term memory. Notes, a wiki filed from them, and rules, as Markdown."""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

import wb_brain
from wb_common import open_path, run_in_background
from wb_settings import Settings


class BrainTab(QWidget):
    def __init__(self, settings: Settings, status):
        super().__init__()
        self.s = settings
        self.status = status
        self.path: Path | None = None
        self.busy = False
        wb_brain.configure(settings)
        wb_brain.ensure()

        title = QLabel("Brain")
        title.setObjectName("title")
        intro = QLabel("What the assistant remembers, as plain Markdown files on this PC. Notes are filed into a wiki "
                       "of topic pages, and every fact says which note it came from and when. Rules are followed in "
                       "every conversation; corrections you repeat become rules once a day. Nothing here goes online.")
        intro.setWordWrap(True)

        self.note = QLineEdit()
        self.note.setPlaceholderText("Remember… e.g. “My cat is called Sixten and is afraid of the vacuum cleaner.”")
        self.note.returnPressed.connect(self.add)
        self.add_btn = QPushButton("Remember")
        self.add_btn.setObjectName("primary")
        self.add_btn.clicked.connect(self.add)
        add_row = QHBoxLayout()
        add_row.addWidget(self.note, 1)
        add_row.addWidget(self.add_btn)

        # Left: search and the files
        self.find = QLineEdit()
        self.find.setPlaceholderText("Search your brain…")
        self.find.returnPressed.connect(self.search)
        self.find.textChanged.connect(lambda t: self.results.setVisible(bool(t.strip()) and self.results.count() > 0))
        self.results = QListWidget()
        self.results.hide()
        self.results.itemClicked.connect(lambda it: self.open_file(Path(it.data(Qt.UserRole))))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._tree_clicked)
        left = QWidget()
        l = QVBoxLayout(left)
        l.setContentsMargins(0, 0, 0, 0)
        l.addWidget(self.find)
        l.addWidget(self.results, 1)
        l.addWidget(self.tree, 2)

        # Right: the file
        self.file_label = QLabel("Pick a page, a note or the rules.")
        self.file_label.setObjectName("muted")
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("")
        self.save_btn = QPushButton("Save changes")
        self.save_btn.clicked.connect(self.save)
        self.tidy_btn = QPushButton("Tidy this page")
        self.tidy_btn.setToolTip("Let the local AI merge facts that say the same thing (sources are kept)")
        self.tidy_btn.clicked.connect(self.tidy)
        self.file_btn = QPushButton("File new notes")
        self.file_btn.setToolTip("File notes that aren't in the wiki yet, e.g. ones written by hand or by another tool")
        self.file_btn.clicked.connect(self.file_unfiled)
        folder = QPushButton("Open folder")
        folder.clicked.connect(lambda: open_path(wb_brain.root()))
        self.map_btn = QPushButton("Brain map")
        self.map_btn.setToolTip("See the brain as a knowledge graph and replay how it grew")
        self.map_btn.clicked.connect(self.show_map)
        self.map_window = None
        file_row = QHBoxLayout()
        for b in (self.save_btn, self.tidy_btn, self.file_btn):
            file_row.addWidget(b)
        file_row.addStretch()
        file_row.addWidget(self.map_btn)
        file_row.addWidget(folder)
        right = QWidget()
        r = QVBoxLayout(right)
        r.setContentsMargins(0, 0, 0, 0)
        r.addWidget(self.file_label)
        r.addWidget(self.editor, 1)
        r.addLayout(file_row)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([330, 770])

        # Bottom: where it's used, learning
        self.in_voice = QCheckBox("Voice assistant remembers and recalls")
        self.in_voice.setChecked(settings.brain_in_voice)
        self.in_experts = QCheckBox("Experts use it (only when answered on this PC)")
        self.in_experts.setChecked(settings.brain_in_experts)
        self.learn_daily = QCheckBox("Learn rules from my corrections once a day")
        self.learn_daily.setChecked(settings.brain_learn_daily)
        for box in (self.in_voice, self.in_experts, self.learn_daily):
            box.toggled.connect(self._save_options)
        self.learn_btn = QPushButton("Learn rules now")
        self.learn_btn.clicked.connect(self.learn)
        opts = QHBoxLayout()
        for w in (self.in_voice, self.in_experts, self.learn_daily):
            opts.addWidget(w)
        opts.addStretch()
        opts.addWidget(self.learn_btn)
        self.stats = QLabel()
        self.stats.setObjectName("muted")
        self.stats.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(intro)
        lay.addLayout(add_row)
        lay.addWidget(split, 1)
        lay.addLayout(opts)
        lay.addWidget(self.stats)
        self.refresh()
        self.open_file(wb_brain.root() / "rules.md")

    # ---- the file list ----------------------------------------------------------------------
    def refresh(self):
        r = wb_brain.root()
        self.tree.clear()
        rules = QTreeWidgetItem(["Rules"])
        rules.setData(0, Qt.UserRole, str(r / "rules.md"))
        index = QTreeWidgetItem(["Index"])
        index.setData(0, Qt.UserRole, str(r / "index.md"))
        log = QTreeWidgetItem(["Log"])
        log.setData(0, Qt.UserRole, str(r / "log.md"))
        wiki = QTreeWidgetItem([f"Wiki ({len(wb_brain.pages())} pages)"])
        for t in wb_brain.pages():
            it = QTreeWidgetItem([f"{t}   ({len(wb_brain.page_facts(t))})"])
            it.setData(0, Qt.UserRole, str(r / "wiki" / f"{t}.md"))
            wiki.addChild(it)
        ns = wb_brain.notes()
        notes = QTreeWidgetItem([f"Notes ({len(ns)})"])
        for n in ns[:500]:
            it = QTreeWidgetItem([n.stem])
            it.setData(0, Qt.UserRole, str(n))
            notes.addChild(it)
        self.tree.addTopLevelItems([rules, wiki, notes, index, log])
        wiki.setExpanded(True)
        st = wb_brain.stats()
        learned = self.s.brain_last_learned.replace("T", " ") or "not yet"
        self.stats.setText(f"{st['notes']} notes · {st['pages']} wiki pages with {st['facts']} facts · "
                           f"{st['rules']} rules · {st['corrections']} corrections waiting · rules last learned: "
                           f"{learned} · {wb_brain.root()}")

    def _tree_clicked(self, item, _col):
        p = item.data(0, Qt.UserRole)
        if p:
            self.open_file(Path(p))

    def open_file(self, path: Path):
        if self.path and self.editor.document().isModified():
            if QMessageBox.question(self, "Brain", f"Save your changes to {self.path.name} first?") \
                    == QMessageBox.Yes:
                self.save()
        self.path = path
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            text = f"(could not read: {e})"
        self.editor.setPlainText(text)
        self.editor.document().setModified(False)
        kind = path.parent.name
        what = {"wiki": "Wiki page", "notes": "Note"}.get(kind, "File")
        self.file_label.setText(f"{what}: {path.name}")
        self.tidy_btn.setEnabled(kind == "wiki")
        self.save_btn.setEnabled(path.name != "log.md" and path.name != "index.md")
        self.editor.setReadOnly(path.name in ("log.md", "index.md"))

    # ---- actions ----------------------------------------------------------------------------
    def show_map(self):
        """The Brain map window (one at a time; it follows the folder while it is open)."""
        from brain_map import BrainMapWindow
        if self.map_window is None:
            self.map_window = BrainMapWindow(self.window(), open_file=self._open_from_map)
            self.map_window.finished.connect(lambda _: setattr(self, "map_window", None))
            self.map_window.setAttribute(Qt.WA_DeleteOnClose)
        self.map_window.show()
        self.map_window.raise_()
        self.map_window.activateWindow()
        return self.map_window

    def _open_from_map(self, path: Path):
        self.open_file(path)
        main = self.window()
        if hasattr(main, "open_tab"):
            main.open_tab("Brain")

    def add(self):
        text = self.note.text().strip()
        if not text or self.busy:
            return
        self.note.clear()
        self._run(lambda emit: wb_brain.remember(text, "typed"), "Filing the note…",
                  lambda path: (self.status(f"Remembered: {path.stem}"), self.open_file(path)))

    def save(self):
        if not self.path or self.editor.isReadOnly():
            return
        text = self.editor.toPlainText()
        path = self.path
        self.editor.document().setModified(False)
        self._run(lambda emit: wb_brain.save_file(path, text), "Saving…", lambda _: self.status(f"Saved {path.name}"))

    def tidy(self):
        if not self.path or self.path.parent.name != "wiki":
            return
        title = self.path.stem
        self._run(lambda emit: wb_brain.tidy_page(title, emit), "Tidying…",
                  lambda n: (self.status(f"Tidied “{title}”: {n} duplicate facts merged"),
                             self.open_file(wb_brain.root() / "wiki" / f"{title}.md")))

    def file_unfiled(self):
        self._run(wb_brain.file_unfiled, "Filing notes…", lambda n: self.status(f"Filed {n} notes"))

    def learn(self):
        def done(rules):
            self.status(f"Learned {len(rules)} new rules" if rules else "No new rules: nothing was repeated or "
                                                                          "asked for as lasting")
            self.open_file(wb_brain.root() / "rules.md")
        self._run(wb_brain.learn_rules, "Learning rules…", done)

    def search(self):
        q = self.find.text().strip()
        if not q:
            return

        def show(hits):
            self.results.clear()
            for h in hits:
                it = QListWidgetItem(f"{h.label}: {h.text[:140]}")
                it.setToolTip(html.escape(h.text))
                it.setData(Qt.UserRole, str(wb_brain.root() / h.source))
                self.results.addItem(it)
            if not hits:
                self.results.addItem("Nothing found.")
            self.results.show()

        run_in_background(lambda emit: wb_brain.search(q, 10), on_result=show)

    def _run(self, fn, message: str, on_result):
        self.busy = True
        for b in (self.add_btn, self.save_btn, self.tidy_btn, self.file_btn, self.learn_btn):
            b.setEnabled(False)
        self.status(message)

        def finished():
            self.busy = False
            for b in (self.add_btn, self.file_btn, self.learn_btn):
                b.setEnabled(True)
            self.refresh()
            if self.path:
                self.save_btn.setEnabled(not self.editor.isReadOnly())
                self.tidy_btn.setEnabled(self.path.parent.name == "wiki")

        run_in_background(fn, on_progress=self.status, on_result=on_result, on_done=finished)

    def _save_options(self):
        self.s.brain_in_voice = self.in_voice.isChecked()
        self.s.brain_in_experts = self.in_experts.isChecked()
        self.s.brain_learn_daily = self.learn_daily.isChecked()
        try:
            self.s.save()
        except OSError:
            pass
