"""The header band at the top of the window, in lundqwist.se's navy and gold (the same look as StoryMaker):
the small tree-and-moon logo and the program's name. The big emblem is shown in an empty conversation."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from wb_common import GOLD, MUTED, NAVY_2

ASSETS = Path(__file__).resolve().parent / "assets"
EMBLEM = ASSETS / "lundqwist-emblem.png"
LOGO = ASSETS / "lundqwist-logo.png"


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
