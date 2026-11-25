"""The list of saved conversations at the right of the window: click one to continue it; each has
Archive and Delete buttons. Archived ones are shown with *Show archived*, where Archive becomes Restore."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout)

import wb_conversations
from wb_common import GOLD, MUTED, NAVY_2, NAVY_3
from wb_settings import Settings


def when(stamp: str) -> str:
    """'14:05' today, 'Yesterday 14:05', 'Mon 14:05' this week, else '24 Nov 2026'."""
    try:
        t = datetime.fromisoformat(stamp)
    except ValueError:
        return ""
    days = (datetime.now().date() - t.date()).days
    if days == 0:
        return t.strftime("%H:%M")
    if days == 1:
        return t.strftime("Yesterday %H:%M")
    if days < 7:
        return t.strftime("%a %H:%M")
    return t.strftime("%d %b %Y").lstrip("0")


class Row(QFrame):
    """One conversation: its title and time, and the Archive (or Restore) and Delete buttons."""

    def __init__(self, c: wb_conversations.Conversation, active: bool, pane: "ConversationsPane"):
        super().__init__()
        self.cid = c.id
        self.setObjectName("convRow")
        border = GOLD if active else NAVY_3
        self.setStyleSheet(f"QFrame#convRow {{ background: {NAVY_2 if active else 'transparent'}; "
                           f"border: 1px solid {border}; border-radius: 5px; }}"
                           " QFrame#convRow QLabel { background: transparent; border: none; }")
        title = QLabel(c.title or "(untitled)")
        title.setWordWrap(True)
        title.setStyleSheet(f"color: {GOLD if active else ''}; font-weight: {'600' if active else 'normal'};")
        title.setToolTip(c.title)
        stamp = QLabel(("● open now · " if active else "") + when(c.updated or c.created))
        stamp.setStyleSheet(f"color: {MUTED}; font-size: 8.5pt;")
        archive = QPushButton("Restore" if c.archived else "Archive")
        archive.setToolTip("Put it back in the list" if c.archived else "Hide it from the list (Show archived finds it)")
        archive.clicked.connect(lambda: pane.archive.emit(self.cid, not c.archived))
        delete = QPushButton("Delete")
        delete.setToolTip("Move it to the Recycle Bin")
        delete.clicked.connect(lambda: pane.delete.emit(self.cid))
        for b in (archive, delete):
            b.setStyleSheet("padding: 1px 8px; font-size: 8.5pt;")
            b.setCursor(Qt.PointingHandCursor)
        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        buttons.addWidget(stamp, 1)
        buttons.addWidget(archive)
        buttons.addWidget(delete)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 6, 6)
        lay.setSpacing(3)
        lay.addWidget(title)
        lay.addLayout(buttons)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to continue this conversation")


class ConversationsPane(QFrame):
    open = Signal(str)            # conversation id
    archive = Signal(str, bool)   # id, archive (True) or restore (False)
    delete = Signal(str)
    new = Signal()
    hide_me = Signal()

    def __init__(self, s: Settings):
        super().__init__()
        self.s = s
        self.active = ""
        self.setObjectName("conversations")
        self.setStyleSheet(f"QFrame#conversations {{ border-left: 1px solid {NAVY_3}; }}")
        self.setMinimumWidth(220)
        title = QLabel("Conversations")
        title.setStyleSheet(f"color: {GOLD}; font-size: 12pt; font-weight: 600;")
        hide = QPushButton("»")
        hide.setFixedWidth(30)
        hide.setStyleSheet("padding: 0px; font-size: 12pt;")
        hide.setToolTip("Hide the list (☰ Conversations above the conversation shows it again)")
        hide.clicked.connect(self.hide_me.emit)
        head = QHBoxLayout()
        head.addWidget(title, 1)
        head.addWidget(hide)
        new = QPushButton("＋  New conversation")
        new.setObjectName("primary")
        new.clicked.connect(self.new.emit)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a conversation…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.refresh())
        self.show_archived = QCheckBox("Show archived")
        self.show_archived.toggled.connect(lambda _: self.refresh())
        self.list = QListWidget()
        self.list.setSpacing(3)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list.setStyleSheet("QListWidget { border: none; background: transparent; }"
                                " QListWidget::item { border: none; } QListWidget::item:selected { background: transparent; }")
        self.list.itemClicked.connect(lambda item: self.open.emit(item.data(Qt.UserRole)))
        self.empty = QLabel("")
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(f"color: {MUTED};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 10, 8, 8)
        lay.addLayout(head)
        lay.addWidget(new)
        lay.addWidget(self.search)
        lay.addWidget(self.show_archived)
        lay.addWidget(self.empty)
        lay.addWidget(self.list, 1)

    def refresh(self, active: str | None = None):
        if active is not None:
            self.active = active
        archived = self.show_archived.isChecked()
        words = self.search.text().lower().split()
        items = [c for c in wb_conversations.listing(self.s, archived)
                 if all(w in c.title.lower() for w in words)]
        self.list.clear()
        for c in items:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, c.id)
            row = Row(c, c.id == self.active, self)
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
        self._fit_rows()
        if items:
            self.empty.setText("")
        elif words:
            self.empty.setText("No conversation title has those words.")
        else:
            self.empty.setText("No archived conversations." if archived else
                               "Conversations are saved here by themselves as you talk or type.")
        self.empty.setVisible(not items)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_rows()

    def _fit_rows(self):
        # rows wrap their titles: their height follows the list's width
        for i in range(self.list.count()):
            item = self.list.item(i)
            row = self.list.itemWidget(item)
            if row:
                width = max(160, self.list.viewport().width() - 2 * self.list.spacing())
                row.setFixedWidth(width)
                item.setSizeHint(QSize(width, row.layout().totalHeightForWidth(width)))
