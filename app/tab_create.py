"""Create tab: pictures, music and stories, each saved with a note of who made it."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QPushButton, QSpinBox, QSplitter,
                               QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

import wb_create
from wb_common import open_path, run_in_background
from wb_settings import Settings

SUFFIX = {"image": "*.png", "music": "*.wav", "story": "*.md"}


class CreatorPage(QWidget):
    """One kind of creation: a request form on top, earlier creations on the left, the result on the right."""

    def __init__(self, s: Settings, status, kind: str, placeholder: str):
        super().__init__()
        self.s, self.status, self.kind = s, status, kind
        self.busy = False
        self.idea = QPlainTextEdit()
        self.idea.setPlaceholderText(placeholder)
        self.idea.setMaximumHeight(70)
        self.options = QHBoxLayout()
        self.go = QPushButton("Create")
        self.go.setObjectName("primary")
        self.go.clicked.connect(self.create)
        self.state = QLabel("")
        self.state.setObjectName("muted")

        self.gallery = QListWidget()
        self.gallery.currentItemChanged.connect(lambda item, _: self.show(Path(item.data(Qt.UserRole))) if item else None)
        open_folder = QPushButton("Open folder")
        open_folder.clicked.connect(lambda: open_path(wb_create.folder(self.s, self.kind)))
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("Made earlier"))
        lv.addWidget(self.gallery, 1)
        lv.addWidget(open_folder)

        self.view = self.make_view()
        self.credit = QLabel("")
        self.credit.setWordWrap(True)
        self.credit.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.credit.setStyleSheet("color: #d4af37;")
        self.open_btn = QPushButton("Open")
        self.open_btn.clicked.connect(lambda: self.path and open_path(self.path))
        self.path: Path | None = None
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(self.view, 1)
        rv.addWidget(self.credit)
        row = QHBoxLayout()
        row.addWidget(self.open_btn)
        row.addStretch()
        rv.addLayout(row)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([260, 840])

        top = QHBoxLayout()
        top.addWidget(self.idea, 1)
        top.addWidget(self.go)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(self.options)
        lay.addWidget(self.state)
        lay.addWidget(split, 1)

    # subclasses: make_view, show_content, work
    def make_view(self) -> QWidget:
        return QTextBrowser()

    def refresh(self, select: Path | None = None):
        self.gallery.blockSignals(True)
        self.gallery.clear()
        for p in sorted(wb_create.folder(self.s, self.kind).glob(SUFFIX[self.kind]), reverse=True):
            rec = wb_create.load_record(p)
            item = QListWidgetItem(f"{p.stem[:8]} {p.stem[9:11]}:{p.stem[11:13]}  {(rec.request if rec else p.stem)[:60]}")
            item.setData(Qt.UserRole, str(p))
            self.gallery.addItem(item)
            if select and p == select:
                self.gallery.setCurrentItem(item)
        self.gallery.blockSignals(False)
        if select:
            self.show(select)

    def show(self, path: Path):
        self.path = path
        rec = wb_create.load_record(path)
        self.credit.setText(rec.credit().replace("\n", "<br>") if rec else "No record of who made this.")
        self.show_content(path)

    def create(self):
        idea = self.idea.toPlainText().strip()
        if not idea or self.busy:
            return
        self.busy = True
        self.go.setEnabled(False)
        self.state.setText("Starting…")

        def done(made):
            self.status(f"Made by {made.by}")
            self.state.setText(f"Saved {made.path.name}")
            self.refresh(select=made.path)

        def finished():
            self.busy = False
            self.go.setEnabled(True)

        run_in_background(self.work, idea, on_progress=self.state.setText, on_result=done,
                          on_error=lambda e: self.state.setText(f"<span style='color:#ff8a80'>Error: {e}</span>"),
                          on_done=finished)


class ImagePage(CreatorPage):
    def __init__(self, s, status):
        super().__init__(s, status, "image", "Describe a picture, in English or Swedish: “a fox reading a book by a "
                                             "campfire, watercolour”")
        self.size = QComboBox()
        self.size.addItems(list(wb_create.SIZES))
        self.improve = QCheckBox("Improve my description first")
        self.improve.setChecked(True)
        self.online = QCheckBox("May use online AI")
        self.online.setChecked(s.online_mode != "off")
        for w in (QLabel("Shape"), self.size, self.improve, self.online):
            self.options.addWidget(w)
        self.options.addStretch()
        self.refresh()

    def make_view(self):
        self.picture = QLabel("Your picture appears here.")
        self.picture.setAlignment(Qt.AlignCenter)
        self.picture.setMinimumHeight(300)
        return self.picture

    def show_content(self, path):
        pix = QPixmap(str(path))
        self.picture.setPixmap(pix.scaled(self.picture.width(), self.picture.height(), Qt.KeepAspectRatio,
                                          Qt.SmoothTransformation))

    def work(self, idea, emit):
        return wb_create.make_image(self.s, idea, self.size.currentText(), self.improve.isChecked(),
                                    online=self.online.isChecked(), emit=emit)


class MusicPage(CreatorPage):
    def __init__(self, s, status):
        super().__init__(s, status, "music", "Describe the music: “calm lo-fi beat with piano and rain”, "
                                             "“epic orchestral battle theme”")
        self.seconds = QSpinBox(minimum=3, maximum=30, value=15)
        self.seconds.setSuffix(" seconds")
        self.improve = QCheckBox("Improve my description first")
        self.improve.setChecked(True)
        for w in (QLabel("Length"), self.seconds, self.improve):
            self.options.addWidget(w)
        self.options.addStretch()
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.refresh()

    def make_view(self):
        box = QWidget()
        v = QVBoxLayout(box)
        self.music_info = QTextBrowser()
        play = QPushButton("▶ Play")
        play.clicked.connect(lambda: self.player.play())
        stop = QPushButton("■ Stop")
        stop.clicked.connect(lambda: self.player.stop())
        row = QHBoxLayout()
        row.addWidget(play)
        row.addWidget(stop)
        row.addStretch()
        v.addLayout(row)
        v.addWidget(self.music_info, 1)
        return box

    def show_content(self, path):
        rec = wb_create.load_record(path)
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        d = rec.details if rec else {}
        self.music_info.setMarkdown(f"**{path.name}**\n\nYour idea: {rec.request if rec else ''}\n\n"
                                    f"Played to the model as: *{d.get('description', '')}*\n\n"
                                    f"{d.get('seconds', '?')} seconds at {d.get('sample_rate', '?')} Hz")

    def work(self, idea, emit):
        return wb_create.make_music(self.s, idea, self.seconds.value(), self.improve.isChecked(), emit=emit)

    def shutdown(self):
        self.player.stop()


class StoryPage(CreatorPage):
    def __init__(self, s, status):
        super().__init__(s, status, "story", "What is the story about? “a robot who is afraid of the dark”, "
                                             "“en drake som vill bli kock”")
        self.genre = QLineEdit()
        self.genre.setPlaceholderText("genre, e.g. fantasy, children's, mystery")
        self.length = QComboBox()
        self.length.addItems(list(wb_create.LENGTHS))
        self.length.setCurrentIndex(1)
        self.language = QComboBox()
        self.language.addItems(["English", "Swedish"])
        self.cover = QCheckBox("Cover picture")
        self.online = QCheckBox("May use online AI")
        self.online.setToolTip("Long stories go online by themselves while tokens last; tick to send this one too")
        for w in (self.genre, self.length, self.language, self.cover, self.online):
            self.options.addWidget(w)
        self.refresh()

    def show_content(self, path):
        self.view.setSearchPaths([str(path.parent.parent / "Images")])
        self.view.setMarkdown(path.read_text(encoding="utf-8"))

    def work(self, idea, emit):
        return wb_create.make_story(self.s, idea, self.genre.text().strip(), self.length.currentText(),
                                    self.language.currentText(), online=True if self.online.isChecked() else None,
                                    cover=self.cover.isChecked(), emit=emit)


class CreateTab(QWidget):
    def __init__(self, settings: Settings, status):
        super().__init__()
        title = QLabel("Create")
        title.setObjectName("title")
        ok, gpu = wb_create.cuda_ready()
        intro = QLabel("Pictures, music and stories. Pictures go to an online image AI while it has tokens, otherwise "
                       f"SDXL-Turbo paints them here; music is made here with MusicGen; stories are written by the "
                       f"writing expert, long ones online. Each one says who made it. "
                       + (f"Graphics card: {gpu}." if ok else f"<span style='color:#ff8a80'>{gpu}.</span>"))
        intro.setWordWrap(True)
        self.pages = QTabWidget()
        self.image = ImagePage(settings, status)
        self.music = MusicPage(settings, status)
        self.story = StoryPage(settings, status)
        self.pages.addTab(self.image, "Picture")
        self.pages.addTab(self.music, "Music")
        self.pages.addTab(self.story, "Story")
        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(intro)
        lay.addWidget(self.pages, 1)
        wb_create.configure(settings, on_made=self.on_made)

    def on_made(self, made):
        """A creation from the voice assistant (another thread): refresh the list on the GUI thread."""
        from PySide6.QtCore import QTimer
        page = {"image": self.image, "music": self.music, "story": self.story}[made.kind]
        QTimer.singleShot(0, self, lambda: page.refresh(select=made.path))

    def refresh(self):
        for p in (self.image, self.music, self.story):
            p.refresh()

    def shutdown(self):
        self.music.shutdown()
