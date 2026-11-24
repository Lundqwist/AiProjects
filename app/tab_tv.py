"""TV tab: find the TV, power, favourites (like CNN on Samsung TV Plus), apps, and a remote."""
from __future__ import annotations

from PySide6.QtCore import QThreadPool, QTimer, Qt
from PySide6.QtWidgets import (QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

import wb_tv
from wb_common import run_in_background
from wb_settings import Settings


class TvTab(QWidget):
    def __init__(self, settings: Settings, status):
        super().__init__()
        self.s = settings
        self.status = status
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)  # commands reach the TV in the order they were given

        title = QLabel("TV")
        title.setObjectName("title")
        self.tv_label = QLabel()
        self.state_label = QLabel()
        find = QPushButton("Find TV")
        find.clicked.connect(self.find)
        top = QHBoxLayout()
        top.addWidget(self.tv_label, 1)
        top.addWidget(self.state_label)
        top.addWidget(find)

        # Power
        self.on_btn = QPushButton("Turn on")
        self.on_btn.setObjectName("primary")
        self.on_btn.clicked.connect(lambda: self.do("Turning on…", lambda tv, emit: (
            "TV is on" if tv.wake() else "The TV didn't wake up. Is 'Power On with Mobile' on?")))
        off = QPushButton("Turn off")
        off.clicked.connect(lambda: self.do("Turning off…", lambda tv, emit: (tv.power_off(), "TV is off")[1]))
        power = QGroupBox("Power")
        p = QHBoxLayout(power)
        p.addWidget(self.on_btn)
        p.addWidget(off)

        # Favourites
        self.favs = QTableWidget(0, 3)
        self.favs.setHorizontalHeaderLabels(["Name", "App", "Channel"])
        self.favs.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.favs.setSelectionBehavior(QTableWidget.SelectRows)
        self.favs.verticalHeader().hide()
        self.favs.doubleClicked.connect(self.watch_selected)
        watch = QPushButton("Watch")
        watch.setObjectName("primary")
        watch.clicked.connect(self.watch_selected)
        add = QPushButton("Add")
        add.clicked.connect(self.add_favorite)
        remove = QPushButton("Remove")
        remove.clicked.connect(self.remove_favorite)
        save = QPushButton("Save list")
        save.clicked.connect(self.save_favorites)
        fav_box = QGroupBox("Favourites (double-click to watch; turns the TV on if needed)")
        f = QVBoxLayout(fav_box)
        f.addWidget(self.favs)
        fr = QHBoxLayout()
        for b in (watch, add, remove, save):
            fr.addWidget(b)
        fr.addStretch()
        f.addLayout(fr)

        # Apps and channel
        self.app = QComboBox()
        self.app.addItems(list(wb_tv.APPS))
        open_app = QPushButton("Open app")
        open_app.clicked.connect(lambda: self.do(f"Opening {self.app.currentText()}…",
                                                 lambda tv, emit, a=self.app.currentText(): (
                                                     tv.watch(app=a, emit=emit))))
        self.channel = QLineEdit()
        self.channel.setPlaceholderText("Channel number")
        self.channel.setMaximumWidth(140)
        self.channel.returnPressed.connect(self.go_channel)
        go = QPushButton("Go to channel")
        go.clicked.connect(self.go_channel)
        apps = QGroupBox("Apps and channels")
        a = QHBoxLayout(apps)
        a.addWidget(self.app, 1)
        a.addWidget(open_app)
        a.addSpacing(20)
        a.addWidget(self.channel)
        a.addWidget(go)

        # Remote
        remote = QGroupBox("Remote")
        g = QGridLayout(remote)
        pad = [("▲", "KEY_UP", 0, 1), ("◀", "KEY_LEFT", 1, 0), ("OK", "KEY_ENTER", 1, 1), ("▶", "KEY_RIGHT", 1, 2),
               ("▼", "KEY_DOWN", 2, 1), ("Back", "KEY_RETURN", 0, 0), ("Home", "KEY_HOME", 0, 2),
               ("Vol +", "KEY_VOLUP", 0, 4), ("Mute", "KEY_MUTE", 1, 4), ("Vol −", "KEY_VOLDOWN", 2, 4),
               ("Ch +", "KEY_CHUP", 0, 5), ("Guide", "KEY_GUIDE", 1, 5), ("Ch −", "KEY_CHDOWN", 2, 5),
               ("Source", "KEY_SOURCE", 2, 0), ("Ch list", "KEY_CH_LIST", 2, 2),
               ("Play/pause", "KEY_PLAY_BACK", 0, 6), ("Live TV", "KEY_TV", 1, 6), ("Info", "KEY_INFO", 2, 6)]
        for text, key, r, c in pad:
            b = QPushButton(text)
            b.setMinimumWidth(80)
            b.clicked.connect(lambda _=False, k=key, t=text: self.do(None, lambda tv, emit: (tv.keys([k]), t)[1]))
            g.addWidget(b, r, c)
        g.setColumnMinimumWidth(3, 20)

        self.log = QLabel("")
        self.log.setObjectName("muted")
        self.log.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addLayout(top)
        row = QHBoxLayout()
        row.addWidget(power)
        row.addWidget(apps, 1)
        lay.addLayout(row)
        lay.addWidget(fav_box, 1)
        lay.addWidget(remote)
        lay.addWidget(self.log)

        self.load_favorites()
        self.show_tv()
        self.timer = QTimer(self, interval=10000, timeout=self.refresh_state)
        self.timer.start()
        self.refresh_state()

    # ---- TV ----------------------------------------------------------------------------
    def tv(self) -> wb_tv.SamsungTV | None:
        return wb_tv.SamsungTV(self.s.tv_ip, self.s.tv_mac) if self.s.tv_ip else None

    def show_tv(self):
        self.tv_label.setText(f"<b>{self.s.tv_name or 'TV'}</b>  ·  {self.s.tv_ip}" if self.s.tv_ip
                              else "No TV chosen yet: turn the TV on and press Find TV.")

    def find(self):
        self.log.setText("Looking for Samsung TVs on your network…")

        def done(tvs):
            if not tvs:
                self.log.setText("No Samsung TV answered. It has to be on, on the same network.")
                return
            tv = tvs[0]
            self.s.tv_ip, self.s.tv_mac, self.s.tv_name = tv.ip, tv.mac, tv.name
            self.s.save()
            self.show_tv()
            self.log.setText(f"Found {tv.name} ({tv.model}) at {tv.ip}. The first command shows an "
                             "'Allow' question on the TV: accept it with the remote.")
            self.refresh_state()

        run_in_background(lambda emit: wb_tv.find(), on_result=done,
                          on_error=lambda e: self.log.setText(f"Search failed: {e}"))

    def refresh_state(self):
        tv = self.tv()
        if not tv:
            self.state_label.setText("")
            return

        def show(state):
            colour = {"on": "#9be59b", "standby": "#d4af37"}.get(state, "#9fb0cc")
            self.state_label.setText(f"<span style='color:{colour}'>● {state}</span>")

        run_in_background(lambda emit: tv.state(), on_result=show, on_error=lambda e: None)

    def do(self, message: str | None, action):
        tv = self.tv()
        if not tv:
            QMessageBox.information(self, "TV", "Turn the TV on and press Find TV first.")
            return
        if message:
            self.log.setText(message)

        def done(result):
            if result:
                self.log.setText(str(result))
            QTimer.singleShot(1500, self.refresh_state)

        run_in_background(lambda emit: action(tv, emit), on_progress=self.log.setText, on_result=done,
                          on_error=lambda e: self.log.setText(f"<span style='color:#ff8a80'>{e}</span>"),
                          pool=self.pool)

    def go_channel(self):
        ch = self.channel.text().strip()
        if ch:
            self.do(f"Channel {ch}…", lambda tv, emit: (tv.channel(ch), f"Channel {ch}")[1])

    # ---- favourites ---------------------------------------------------------------------
    def load_favorites(self):
        self.favs.setRowCount(0)
        for fav in self.s.tv_favorites:
            self._add_row(fav.get("name", ""), fav.get("app", ""), fav.get("channel", ""))

    def _add_row(self, name="", app="", channel=""):
        r = self.favs.rowCount()
        self.favs.insertRow(r)
        self.favs.setItem(r, 0, QTableWidgetItem(name))
        combo = QComboBox()
        combo.addItems([""] + list(wb_tv.APPS))
        combo.setEditable(True)
        combo.setCurrentText(app)
        self.favs.setCellWidget(r, 1, combo)
        self.favs.setItem(r, 2, QTableWidgetItem(channel))

    def _row(self, r) -> dict:
        get = lambda c: (self.favs.item(r, c).text().strip() if self.favs.item(r, c) else "")  # noqa: E731
        return {"name": get(0), "app": self.favs.cellWidget(r, 1).currentText().strip(), "channel": get(2)}

    def add_favorite(self):
        self._add_row("New favourite", "Samsung TV Plus", "")
        self.favs.editItem(self.favs.item(self.favs.rowCount() - 1, 0))

    def remove_favorite(self):
        for r in sorted({i.row() for i in self.favs.selectedIndexes()}, reverse=True):
            self.favs.removeRow(r)
        self.save_favorites()

    def save_favorites(self):
        self.s.tv_favorites = [f for f in (self._row(r) for r in range(self.favs.rowCount())) if f["name"]]
        self.s.save()
        self.log.setText(f"Saved {len(self.s.tv_favorites)} favourite(s). The voice assistant knows them too.")

    def watch_selected(self):
        rows = sorted({i.row() for i in self.favs.selectedIndexes()})
        if not rows:
            return
        fav = self._row(rows[0])
        self.do(f"{fav['name']}…", lambda tv, emit: tv.watch(fav["app"], fav["channel"], emit))
