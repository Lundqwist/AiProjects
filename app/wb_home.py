"""The front page and the header band, in lundqwist.se's navy and gold (the same look as StoryMaker):
the big Lundqwist emblem, the program's name, and a card for every tool that opens its tab."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from wb_common import GOLD, MUTED, NAVY, NAVY_2, NAVY_3

ASSETS = Path(__file__).resolve().parent / "assets"
EMBLEM = ASSETS / "lundqwist-emblem.png"
LOGO = ASSETS / "lundqwist-logo.png"


class Emblem(QWidget):
    """The emblem, scaled to the page's width, with its edges fading into the navy page."""

    def __init__(self):
        super().__init__()
        self.pix = QPixmap(str(EMBLEM))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        if self.pix.isNull():
            return 0
        ew = min(max(w - 80, 300), 880)
        return ew * self.pix.height() // self.pix.width()

    def sizeHint(self) -> QSize:
        return QSize(880, self.heightForWidth(960))

    def paintEvent(self, _):
        if self.pix.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        w = self.width()
        ew = min(max(w - 80, 300), 880)
        eh = ew * self.pix.height() // self.pix.width()
        r = QRectF((w - ew) / 2, 0, ew, eh)
        p.drawPixmap(r.toRect(), self.pix)
        navy, clear = QColor(NAVY), QColor(NAVY)
        clear.setAlpha(0)
        fx, fy = r.width() / 7, r.height() / 6

        def fade(area: QRectF, x1, y1, x2, y2):
            g = QLinearGradient(x1, y1, x2, y2)
            g.setColorAt(0, navy)
            g.setColorAt(1, clear)
            p.fillRect(area.adjusted(-1, -1, 1, 1), g)

        fade(QRectF(r.left(), r.top(), fx, r.height()), r.left(), 0, r.left() + fx, 0)
        fade(QRectF(r.right() - fx, r.top(), fx, r.height()), r.right(), 0, r.right() - fx, 0)
        fade(QRectF(r.left(), r.top(), r.width(), fy), 0, r.top(), 0, r.top() + fy)
        fade(QRectF(r.left(), r.bottom() - fy, r.width(), fy), 0, r.bottom(), 0, r.bottom() - fy)
        p.end()


class Card(QFrame):
    """One tool on the front page: a gold-framed card. Click it to open that tool's tab."""
    clicked = Signal()

    def __init__(self, title: str, line: str):
        super().__init__()
        self.setObjectName("homeCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(300, 96)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 12, 14, 12)
        t = QLabel(title)
        t.setStyleSheet(f"color: {GOLD}; font-family: Georgia; font-size: 14pt; font-weight: bold; background: transparent;")
        d = QLabel(line)
        d.setWordWrap(True)
        d.setStyleSheet(f"color: {MUTED}; font-size: 9.5pt; background: transparent;")
        lay.addWidget(t)
        lay.addWidget(d, 1)
        self.setAccessibleName(f"{title}: {line}")
        self.setStyleSheet(f"""
            QFrame#homeCard {{ background: {NAVY_2}; border: 1px solid {GOLD}; border-radius: 8px; }}
            QFrame#homeCard:hover {{ background: {NAVY_3}; border: 2px solid #e6c45a; }}""")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class Divider(QWidget):
    """A soft gold line under the title, like the site's dividers."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(12)

    def paintEvent(self, _):
        p = QPainter(self)
        c = QColor(GOLD)
        c.setAlpha(120)
        p.setPen(QPen(c, 1))
        mid = self.width() // 2
        p.drawLine(mid - 120, 6, mid + 120, 6)
        p.end()


class HomePage(QScrollArea):
    """The front page. `open_tab` is emitted with a tab's name when its card is clicked."""
    open_tab = Signal(str)

    def __init__(self, tools: list[tuple[str, str]]):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 12, 0, 24)
        lay.setSpacing(0)
        lay.addWidget(Emblem())

        title = QLabel("AI Workbench")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {GOLD}; font-family: Georgia; font-size: 26pt;")
        sub = QLabel("Local AI on my own PC: documents, voice, invoices, research and more")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(f"color: {MUTED}; font-size: 11.5pt; font-style: italic;")
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(6)
        lay.addWidget(Divider())
        lay.addSpacing(16)

        self.cards = [Card(name, line) for name, line in tools]
        for card, (name, _) in zip(self.cards, tools):
            card.clicked.connect(lambda n=name: self.open_tab.emit(n))
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(20)
        self.grid.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.grid_host)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        self.setWidget(page)
        self._per_row = 0
        self._arrange()

    def _arrange(self):
        per_row = max(1, min(len(self.cards), (self.viewport().width() - 40) // 320))
        if per_row == self._per_row:
            return
        self._per_row = per_row
        for c in self.cards:
            self.grid.removeWidget(c)
        for i, c in enumerate(self.cards):
            self.grid.addWidget(c, i // per_row, i % per_row)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._arrange()


class HeaderBand(QFrame):
    """The band at the top of the window, as on lundqwist.se: the tree-and-moon logo and the name."""
    home_clicked = Signal()

    def __init__(self, subtitle: str):
        super().__init__()
        self.setObjectName("headerBand")
        self.setFixedHeight(58)
        self.setStyleSheet(f"QFrame#headerBand {{ background: {NAVY_2}; border: none; border-bottom: 1px solid {GOLD}; }}"
                           f" QFrame#headerBand QLabel {{ background: transparent; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 16, 6)
        lay.setSpacing(10)
        logo = QLabel()
        pix = QPixmap(str(LOGO))
        if not pix.isNull():
            logo.setPixmap(pix.scaled(42, 42, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation))
        logo.setCursor(Qt.CursorShape.PointingHandCursor)
        logo.setToolTip("Home")
        logo.mouseReleaseEvent = lambda e: self.home_clicked.emit()
        names = QVBoxLayout()
        names.setSpacing(0)
        name = QLabel("AI Workbench")
        name.setStyleSheet(f"color: {GOLD}; font-family: Georgia; font-size: 17pt;")
        small = QLabel(subtitle)
        small.setStyleSheet(f"color: {MUTED}; font-size: 9pt;")
        names.addWidget(name)
        names.addWidget(small)
        lay.addWidget(logo)
        lay.addLayout(names)
        lay.addStretch(1)
