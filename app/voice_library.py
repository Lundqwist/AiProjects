"""Voice library dialog: browse Piper voices, listen, download, and choose one."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView,
                               QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

import wb_voices
from wb_common import run_in_background


class VoiceLibrary(QDialog):
    """Returns the chosen voice in .chosen as (lang, name) when accepted."""

    def __init__(self, parent, voices_dir: str, lang: str, current: str, speed=1.0, volume=1.0, device=None):
        super().__init__(parent)
        self.setWindowTitle("Voice library")
        self.resize(720, 520)
        self.voices_dir, self.speed, self.volume, self.device = voices_dir, speed, volume, device
        self.current = current
        self.chosen: tuple[str, str] | None = None

        self.lang = QComboBox()
        self.lang.addItem("English", "en")
        self.lang.addItem("Swedish", "sv")
        self.lang.setCurrentIndex(0 if lang == "en" else 1)
        self.lang.currentIndexChanged.connect(self.load)
        self.only_female = QComboBox()
        self.only_female.addItems(["All voices", "Female voices", "Male voices"])
        self.only_female.currentIndexChanged.connect(self.load)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Voice", "Speaker", "Size", "On this PC"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self.listen)

        self.status = QLabel("Loading the voice list…")
        self.status.setObjectName("muted")
        listen = QPushButton("Listen")
        listen.clicked.connect(self.listen)
        use = QPushButton("Use this voice")
        use.setObjectName("primary")
        use.clicked.connect(self.use)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)

        top = QHBoxLayout()
        top.addWidget(QLabel("Language"))
        top.addWidget(self.lang)
        top.addWidget(self.only_female)
        top.addStretch()
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(listen)
        bottom.addWidget(use)
        bottom.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(QLabel("Listening to a voice that isn't on this PC downloads it first (about 60 MB)."))
        lay.addWidget(self.table, 1)
        lay.addLayout(bottom)
        self.load()

    def _lang(self) -> str:
        return self.lang.currentData()

    def load(self):
        lang = self._lang()
        want = {0: None, 1: "female", 2: "male"}[self.only_female.currentIndex()]

        def work(emit):
            try:
                return wb_voices.available(lang), None
            except Exception as e:  # offline: show what's installed
                return [{"name": n, "label": wb_voices.label(n), "gender": wb_voices.gender(n), "size_mb": 0}
                        for n in wb_voices.installed(self.voices_dir, lang)], str(e)

        def show(result):
            voices, error = result
            have = set(wb_voices.installed(self.voices_dir))
            voices = [v for v in voices if not want or v["gender"] == want]
            self.table.setRowCount(len(voices))
            for r, v in enumerate(voices):
                cells = [v["label"] + ("   ← in use" if v["name"] == self.current else ""), v["gender"],
                         f"{v['size_mb']} MB" if v["size_mb"] else "", "yes" if v["name"] in have else ""]
                for c, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    item.setData(Qt.UserRole, v["name"])
                    self.table.setItem(r, c, item)
                if v["name"] == self.current:
                    self.table.selectRow(r)
            self.status.setText(f"Offline: showing installed voices only ({error})" if error
                                else f"{len(voices)} voices")

        run_in_background(work, on_result=show)

    def _selected(self) -> str | None:
        items = self.table.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def _ensure(self, name: str, then):
        if name in wb_voices.installed(self.voices_dir):
            then()
            return
        self.status.setText(f"Downloading {name}…")

        def work(emit):
            wb_voices.download(name, self.voices_dir)

        def done(_):
            self.status.setText(f"Downloaded {name}")
            self.load()
            then()

        run_in_background(work, on_result=done, on_error=lambda e: self.status.setText(f"Download failed: {e}"))

    def listen(self):
        name = self._selected()
        if not name:
            return

        def play():
            self.status.setText(f"Playing {wb_voices.label(name)}…")
            run_in_background(lambda emit: wb_voices.preview(name, self.voices_dir, self.speed, self.volume,
                                                             self.device),
                              on_done=lambda: self.status.setText(""))

        self._ensure(name, play)

    def use(self):
        name = self._selected()
        if not name:
            return

        def choose():
            self.chosen = (self._lang(), name)
            self.accept()

        self._ensure(name, choose)
