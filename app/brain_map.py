"""Brain map: a window that draws the brain as a living knowledge graph and replays how it grew.

Wiki pages are gold (bigger = more facts), notes blue, rules green around a Rules hub. Lines show
where facts came from (note → page), notes that outdated a fact (red), and pages that belong
together. The timeline at the bottom goes back to any moment; Play replays the growth, and while
the window is open it follows the Brain folder, so new notes light up as they are filed.
"""
from __future__ import annotations

import html
import math
import time
from datetime import datetime, timedelta

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton,
                               QSlider, QSplitter, QTextBrowser, QToolTip, QVBoxLayout, QWidget)

import wb_brain_map
from wb_common import GOLD, MUTED, NAVY, NAVY_2, NAVY_3, TEXT

NOTE_C, NOTE_DIM, RULE_C, OLD_C = QColor("#7fb3ff"), QColor("#5a6b88"), QColor("#7ddc9a"), QColor("#ff8a80")
EDGE_C = {"filed": QColor(159, 176, 204, 70), "outdated": QColor(255, 138, 128, 110),
          "related": QColor(212, 175, 55, 80), "rule": QColor(125, 220, 154, 70)}
K = 55.0                             # the layout's natural edge length
SLIDER_MAX = 1000
GLOW_SECONDS = 5.0


class GraphCanvas(QWidget):
    """The graph: force-directed layout, zoom with the wheel, drag the background to pan, drag a node to move it."""
    selected = Signal(str)           # a node id, or "" when the selection is cleared
    opened = Signal(str)

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self.setMinimumSize(500, 360)
        self.graph: wb_brain_map.Graph | None = None
        self.ids: list[str] = []
        self.pos = np.zeros((0, 2))
        self.placed = np.zeros(0, bool)
        self.visible = np.zeros(0, bool)
        self.t = datetime.now()
        self.show_notes = self.show_rules = True
        self.sel = ""
        self.hover = ""
        self.match: set[str] = set()
        self.fresh: dict[str, float] = {}          # node id → when it appeared live (for a glow)
        self.recent: set[str] = set()              # appeared just before the timeline's moment
        self.scale, self.offset = 1.0, QPointF(0, 0)
        self.user_moved_view = False
        self.temp = 0.0
        self.drag: tuple | None = None
        self.timer = QTimer(self, interval=33, timeout=self._tick)

    # ---- data -------------------------------------------------------------------------------
    def set_graph(self, g: wb_brain_map.Graph):
        old = {i: self.pos[k] for k, i in enumerate(self.ids) if self.placed[k]}
        self.graph = g
        self.ids = list(g.nodes)
        self.index = {i: k for k, i in enumerate(self.ids)}
        self.pos = np.zeros((len(self.ids), 2))
        self.placed = np.zeros(len(self.ids), bool)
        for k, i in enumerate(self.ids):
            if i in old:
                self.pos[k], self.placed[k] = old[i], True
        self.edge_index = np.array([(self.index[e.a], self.index[e.b]) for e in g.edges], int).reshape(-1, 2)
        self.edge_w = np.array([{"filed": 1.0, "outdated": 0.6, "rule": 1.2}.get(e.kind, min(e.weight, 3) * 0.5)
                                for e in g.edges]).reshape(-1)
        self.edge_t = [e.t for e in g.edges]
        self.nbrs: dict[str, set[str]] = {i: set() for i in self.ids}
        for e in g.edges:
            self.nbrs[e.a].add(e.b)
            self.nbrs[e.b].add(e.a)
        if self.sel not in g.nodes:
            self.sel = ""

    def set_time(self, t: datetime, glow_window: float = 0.0):
        self.t = t
        self._update_visible()
        g = self.graph
        if g and glow_window > 0:
            span = (g.end - g.start).total_seconds() or 1
            self.recent = {i for i, n in g.nodes.items()
                           if 0 <= (t - n.t).total_seconds() <= span * glow_window}
        else:
            self.recent = set()
        self.update()

    def _update_visible(self):
        g = self.graph
        if not g:
            return
        vis = np.array([n.t <= self.t and (self.show_notes or n.kind != "note") and
                        (self.show_rules or n.kind not in ("rule", "hub")) for n in g.nodes.values()], bool)
        new = vis & ~self.placed
        rng = np.random.default_rng(len(self.ids))
        for k in np.nonzero(new)[0]:  # a new node starts next to one it is linked to
            near = [self.index[n] for n in self.nbrs[self.ids[k]] if self.placed[self.index[n]]]
            base = self.pos[near[0]] if near else np.zeros(2)
            spread = K if near else K * 3
            self.pos[k] = base + rng.normal(0, spread, 2)
            self.placed[k] = True
        self.visible = vis
        if new.any():
            self.heat(min(25.0, 8.0 + new.sum()))

    def heat(self, temp: float):
        self.temp = max(self.temp, temp)
        if not self.timer.isActive():
            self.timer.start()

    def settle(self, steps: int):
        """Run the layout without drawing, e.g. before the window first shows."""
        self.heat(30.0)
        for _ in range(steps):
            if not self._step():
                break

    # ---- layout -----------------------------------------------------------------------------
    def _step(self) -> bool:
        idx = np.nonzero(self.visible)[0]
        if len(idx) == 0 or self.temp < 0.3:
            return False
        local = -np.ones(len(self.ids), int)
        local[idx] = np.arange(len(idx))
        P = self.pos[idx]
        d = P[:, None, :] - P[None, :, :]
        dist2 = (d ** 2).sum(-1) + 1.0
        near = dist2 < (5 * K) ** 2                       # far-off islands don't push each other away
        disp = (d / dist2[..., None] * near[..., None]).sum(1) * K * K
        if len(self.edge_index):
            ok = (local[self.edge_index[:, 0]] >= 0) & (local[self.edge_index[:, 1]] >= 0)
            ea, eb = local[self.edge_index[ok, 0]], local[self.edge_index[ok, 1]]
            w = self.edge_w[ok][:, None]
            delta = P[ea] - P[eb]
            dist = np.sqrt((delta ** 2).sum(-1, keepdims=True)) + 0.01
            f = delta * dist / K * w
            np.add.at(disp, ea, -f)
            np.add.at(disp, eb, f)
        # gravity keeps unconnected islands close: the graph's radius then grows as n^(1/4), not n^(1/2)
        disp -= P * 0.7 * math.sqrt(len(idx)) / K
        length = np.sqrt((disp ** 2).sum(-1, keepdims=True)) + 0.01
        disp = disp / length * np.minimum(length, self.temp)
        if self.drag and self.drag[0] == "node":
            disp[local[self.index[self.drag[1]]]] = 0
        self.pos[idx] = P + disp
        self.temp *= 0.96
        return True

    def _tick(self):
        moving = self._step()
        if not moving and not self.fresh:
            self.timer.stop()
            if not self.user_moved_view:
                self.fit()
        now = time.monotonic()
        self.fresh = {i: t for i, t in self.fresh.items() if now - t < GLOW_SECONDS}
        self.update()

    def fit(self):
        idx = np.nonzero(self.visible)[0]
        if len(idx) == 0:
            self.scale, self.offset = 1.0, QPointF(self.width() / 2, self.height() / 2)
        else:
            P = self.pos[idx]
            lo, hi = P.min(0) - K, P.max(0) + K
            w, h = max(hi[0] - lo[0], 1), max(hi[1] - lo[1], 1)
            self.scale = max(0.15, min(2.5, min(self.width() / w, self.height() / h)))
            c = (lo + hi) / 2
            self.offset = QPointF(self.width() / 2 - c[0] * self.scale, self.height() / 2 - c[1] * self.scale)
        self.user_moved_view = False
        self.update()

    # ---- drawing ----------------------------------------------------------------------------
    def _screen(self, k: int) -> QPointF:
        return QPointF(self.pos[k][0] * self.scale + self.offset.x(), self.pos[k][1] * self.scale + self.offset.y())

    def radius(self, node) -> float:
        if node.kind == "page":
            return 7 + 3.2 * math.sqrt(len(node.active_facts(self.t)))
        return {"note": 4.5, "rule": 6.0, "hub": 13.0}[node.kind]

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(NAVY))
        g = self.graph
        if not g or not self.visible.any():
            p.setPen(QColor(MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Nothing in the brain yet at this moment.\n"
                       "Tell the assistant something to remember, or add a note in the Brain tab.")
            return
        focus = ({self.sel} | self.nbrs[self.sel]) if self.sel else set()
        for (a, b), e, t in zip(self.edge_index, g.edges, self.edge_t):
            if not (self.visible[a] and self.visible[b]) or t > self.t:
                continue
            c = QColor(EDGE_C[e.kind])
            width = 1.0 if e.kind != "related" else 0.8 + 0.6 * min(e.weight, 4)
            if focus:
                if e.a in focus and e.b in focus and self.sel in (e.a, e.b):
                    c.setAlpha(220)
                    width += 0.8
                else:
                    c.setAlpha(18)
            pen = QPen(c, width)
            if e.kind == "outdated":
                pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.drawLine(self._screen(a), self._screen(b))

        now = time.monotonic()
        label_font = QFont("Segoe UI", 9)
        p.setFont(label_font)
        order = sorted(np.nonzero(self.visible)[0], key=lambda k: g.nodes[self.ids[k]].kind == "page")
        for k in order:
            i = self.ids[k]
            n = g.nodes[i]
            c = {"page": QColor(GOLD), "note": NOTE_C if n.filed else NOTE_DIM, "rule": RULE_C, "hub": RULE_C}[n.kind]
            c = QColor(c)
            if focus and i not in focus:
                c.setAlpha(45)
            r = self.radius(n)
            pt = self._screen(k)
            rs = max(2.5, r * min(1.6, max(0.55, self.scale)))
            glow = i in self.recent or (i in self.fresh and now - self.fresh[i] < GLOW_SECONDS)
            if glow:
                a = 1.0 if i in self.recent else 1 - (now - self.fresh[i]) / GLOW_SECONDS
                halo = QColor(c)
                halo.setAlpha(int(90 * a))
                p.setPen(Qt.NoPen)
                p.setBrush(halo)
                p.drawEllipse(pt, rs * 2.2, rs * 2.2)
            p.setBrush(c)
            ring = i == self.sel or i == self.hover or i in self.match
            p.setPen(QPen(QColor(TEXT), 2) if ring else QPen(QColor(NAVY_2), 1))
            if n.kind == "rule":
                p.drawPolygon(QPolygonF([QPointF(pt.x(), pt.y() - rs), QPointF(pt.x() + rs, pt.y()),
                                         QPointF(pt.x(), pt.y() + rs), QPointF(pt.x() - rs, pt.y())]))
            else:
                p.drawEllipse(pt, rs, rs)
            show_label = (n.kind in ("page", "hub") and (self.scale > 0.45 or len(n.facts) > 3)) or ring or \
                (focus and i in focus and n.kind != "note")
            if show_label:
                text = n.title if len(n.title) <= 34 else n.title[:32] + "…"
                tc = QColor(TEXT if n.kind != "note" else NOTE_C)
                if focus and i not in focus:
                    tc.setAlpha(60)
                p.setPen(tc)
                p.drawText(QRectF(pt.x() - 120, pt.y() + rs + 2, 240, 18), Qt.AlignHCenter | Qt.AlignTop, text)

    # ---- mouse ------------------------------------------------------------------------------
    def node_at(self, pos: QPointF) -> str:
        if not self.graph:
            return ""
        best, best_d = "", 1e9
        for k in np.nonzero(self.visible)[0]:
            s = self._screen(k)
            d = math.hypot(s.x() - pos.x(), s.y() - pos.y())
            r = max(6.0, self.radius(self.graph.nodes[self.ids[k]]) * min(1.6, max(0.55, self.scale)) + 3)
            if d < r and d < best_d:
                best, best_d = self.ids[k], d
        return best

    def mousePressEvent(self, ev):
        i = self.node_at(ev.position())
        self.drag = ("node", i, ev.position(), False) if i else ("pan", "", ev.position(), False)

    def mouseMoveEvent(self, ev):
        if self.drag:
            kind, i, start, _ = self.drag
            if (ev.position() - start).manhattanLength() > 3:
                self.drag = (kind, i, start, True)
            if self.drag[3]:
                if kind == "node":
                    k = self.index[i]
                    self.pos[k] = [(ev.position().x() - self.offset.x()) / self.scale,
                                   (ev.position().y() - self.offset.y()) / self.scale]
                    self.heat(4.0)
                else:
                    self.offset += ev.position() - start
                    self.drag = (kind, i, ev.position(), True)
                    self.user_moved_view = True
                self.update()
            return
        i = self.node_at(ev.position())
        if i != self.hover:
            self.hover = i
            self.setCursor(Qt.PointingHandCursor if i else Qt.ArrowCursor)
            if i:
                QToolTip.showText(ev.globalPosition().toPoint(), self.tooltip(i), self)
            else:
                QToolTip.hideText()
            self.update()

    def mouseReleaseEvent(self, ev):
        if self.drag and not self.drag[3]:
            self.sel = self.drag[1] if self.drag[1] != self.sel else ""
            self.selected.emit(self.sel)
            self.update()
        self.drag = None

    def mouseDoubleClickEvent(self, ev):
        i = self.node_at(ev.position())
        if i:
            self.opened.emit(i)

    def wheelEvent(self, ev):
        f = 1.15 ** (ev.angleDelta().y() / 120)
        new = max(0.1, min(6.0, self.scale * f))
        f = new / self.scale
        pos = ev.position()
        self.offset = pos - (pos - self.offset) * f
        self.scale = new
        self.user_moved_view = True
        self.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if not self.user_moved_view:
            self.fit()

    def tooltip(self, i: str) -> str:
        n = self.graph.nodes[i]
        when = f"{n.t:%Y-%m-%d %H:%M}"
        if n.kind == "page":
            act = len(n.active_facts(self.t))
            return f"<b>{html.escape(n.title)}</b><br>wiki page · {act} fact{'s' if act != 1 else ''} · since {when}"
        if n.kind == "note":
            return f"<b>{html.escape(n.title)}</b><br>note · {when}{'' if n.filed else ' · not filed yet'}"
        if n.kind == "rule":
            return f"<b>Rule</b> ({'learned' if n.learned else 'yours'}) · {when}<br>{html.escape(n.title)}"
        return "<b>Rules</b><br>what the assistant always follows"


class GrowthChart(QWidget):
    """Notes, facts, pages and rules over time; click or drag to move the timeline."""
    picked = Signal(float)            # 0..1 along the timeline

    SERIES = (("facts", QColor(GOLD), "facts"), ("notes", NOTE_C, "notes"), ("pages", QColor(TEXT), "pages"),
              ("rules", RULE_C, "rules"))

    def __init__(self):
        super().__init__()
        self.setFixedHeight(120)
        self.samples: list[dict] = []
        self.at = 1.0

    def set_graph(self, g: wb_brain_map.Graph, n: int = 160):
        span = (g.end - g.start).total_seconds()
        if span <= 0:
            self.samples = [g.counts_at(g.end)] * 2
        else:
            self.samples = [g.counts_at(g.start + timedelta(seconds=span * i / (n - 1))) for i in range(n)]
        self.update()

    def set_at(self, frac: float):
        self.at = frac
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(NAVY_2))
        left, top, w, h = 8, 22, self.width() - 16, self.height() - 30
        p.setPen(QColor(MUTED))
        p.setFont(QFont("Segoe UI", 8))
        x = left
        for key, c, label in self.SERIES:  # legend with the values at the timeline's moment
            val = self._value_at(key)
            p.setPen(c)
            p.drawText(QPointF(x, 14), f"● {label} {val}")
            x += 90
        if not self.samples:
            return
        top_val = max(1, max(max(s[k] for k, _, _ in self.SERIES) for s in self.samples))
        n = len(self.samples)
        for key, c, _ in self.SERIES:
            path = QPainterPath()
            for i, s in enumerate(self.samples):
                pt = QPointF(left + w * i / (n - 1), top + h - h * s[key] / top_val)
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            p.setPen(QPen(c, 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        xm = left + w * self.at
        p.setPen(QPen(QColor(GOLD), 1, Qt.DashLine))
        p.drawLine(QPointF(xm, top - 4), QPointF(xm, top + h))

    def _value_at(self, key: str) -> int:
        if not self.samples:
            return 0
        return self.samples[round(self.at * (len(self.samples) - 1))][key]

    def _pick(self, ev):
        w = self.width() - 16
        self.picked.emit(max(0.0, min(1.0, (ev.position().x() - 8) / max(1, w))))

    def mousePressEvent(self, ev):
        self._pick(ev)

    def mouseMoveEvent(self, ev):
        if ev.buttons():
            self._pick(ev)


class BrainMapWindow(QDialog):
    def __init__(self, parent=None, open_file=None):
        super().__init__(parent)
        self.setWindowTitle("Brain map · AI Workbench")
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.resize(1250, 820)
        self.open_file = open_file
        self.graph: wb_brain_map.Graph | None = None
        self.sig = None

        title = QLabel("Brain map")
        title.setObjectName("title")
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        self.find = QLineEdit()
        self.find.setPlaceholderText("Find a page, note or rule…")
        self.find.setClearButtonEnabled(True)
        self.find.textChanged.connect(self._find)
        self.find.returnPressed.connect(self._select_first_match)
        self.notes_box = QCheckBox("Notes")
        self.rules_box = QCheckBox("Rules")
        for b in (self.notes_box, self.rules_box):
            b.setChecked(True)
            b.toggled.connect(self._filters)
        fit = QPushButton("Fit")
        fit.setToolTip("Show the whole graph")
        fit.clicked.connect(lambda: self.canvas.fit())
        shake = QPushButton("Re-arrange")
        shake.setToolTip("Let the layout settle again")
        shake.clicked.connect(lambda: (self.canvas.heat(30.0), setattr(self.canvas, "user_moved_view", False)))
        top = QHBoxLayout()
        top.addWidget(title)
        top.addSpacing(12)
        top.addWidget(self.summary, 1)
        top.addWidget(self.find)
        for w in (self.notes_box, self.rules_box, fit, shake):
            top.addWidget(w)

        self.canvas = GraphCanvas()
        self.canvas.selected.connect(self._show_node)
        self.canvas.opened.connect(self._open_node)
        self.details = QTextBrowser()
        self.details.setOpenLinks(False)
        self.details.anchorClicked.connect(lambda url: self._select(url.toString()))
        act_label = QLabel("What happened")
        act_label.setObjectName("muted")
        self.activity = QListWidget()
        self.activity.setWordWrap(True)
        self.activity.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(self.details, 3)
        sl.addWidget(act_label)
        sl.addWidget(self.activity, 2)
        split = QSplitter()
        split.addWidget(self.canvas)
        split.addWidget(side)
        split.setStretchFactor(0, 1)
        split.setSizes([880, 340])

        self.chart = GrowthChart()
        self.chart.picked.connect(lambda f: self.slider.setValue(round(f * SLIDER_MAX)))
        self.play_btn = QPushButton("▶ Play")
        self.play_btn.setToolTip("Replay how the brain grew, from the first note to now")
        self.play_btn.clicked.connect(self.toggle_play)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, SLIDER_MAX)
        self.slider.setValue(SLIDER_MAX)
        self.slider.valueChanged.connect(self._slid)
        self.when = QLabel()
        self.when.setMinimumWidth(150)
        now_btn = QPushButton("Now")
        now_btn.clicked.connect(lambda: (self._stop(), self.slider.setValue(SLIDER_MAX)))
        self.live = QLabel()
        self.live.setObjectName("muted")
        tl = QHBoxLayout()
        tl.addWidget(self.play_btn)
        tl.addWidget(self.slider, 1)
        tl.addWidget(self.when)
        tl.addWidget(now_btn)
        tl.addWidget(self.live)

        legend = QLabel(f"<span style='color:{GOLD}'>●</span> wiki page (size = facts) &nbsp; "
                        f"<span style='color:{NOTE_C.name()}'>●</span> note &nbsp; "
                        f"<span style='color:{NOTE_DIM.name()}'>●</span> note not filed yet &nbsp; "
                        f"<span style='color:{RULE_C.name()}'>◆</span> rule &nbsp; "
                        f"<span style='color:{OLD_C.name()}'>- -</span> note that outdated a fact &nbsp; "
                        f"<span style='color:{GOLD}'>—</span> related pages &nbsp;·&nbsp; "
                        "click to see a node, double-click to open its file, drag to move, wheel to zoom")
        legend.setObjectName("muted")

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(split, 1)
        lay.addWidget(self.chart)
        lay.addLayout(tl)
        lay.addWidget(legend)

        self.play_timer = QTimer(self, interval=40, timeout=self._play_step)
        self.watch = QTimer(self, interval=3000, timeout=self.reload_if_changed)
        self.reload(first=True)
        self.watch.start()

    # ---- loading ----------------------------------------------------------------------------
    def reload(self, first: bool = False):
        at_end = self.slider.value() == SLIDER_MAX
        before = set(self.graph.nodes) if self.graph else set()
        self.sig = wb_brain_map.signature()
        self.graph = g = wb_brain_map.build()
        self.canvas.set_graph(g)
        self.chart.set_graph(g)
        if at_end:
            self.slider.blockSignals(True)
            self.slider.setValue(SLIDER_MAX)
            self.slider.blockSignals(False)
        self.slider.setEnabled(g.end > g.start)
        self.play_btn.setEnabled(g.end > g.start)
        self._slid(self.slider.value())
        if first:
            self.canvas.settle(min(200, 60 + 20000 // max(50, len(g.nodes))))
            self.canvas.timer.start()
        else:
            now = time.monotonic()
            for i in set(g.nodes) - before:
                self.canvas.fresh[i] = now
            if set(g.nodes) - before:
                self.canvas.heat(10.0)
        self.live.setText(f"live · updated {datetime.now():%H:%M:%S}")
        self._find(self.find.text())
        self._show_node(self.canvas.sel)

    def reload_if_changed(self):
        if wb_brain_map.signature() != self.sig:
            self.reload()

    # ---- the timeline -----------------------------------------------------------------------
    def time_at(self, v: int) -> datetime:
        g = self.graph
        return g.start + (g.end - g.start) * (v / SLIDER_MAX)

    def _slid(self, v: int):
        g = self.graph
        if not g:
            return
        t = self.time_at(v) if v < SLIDER_MAX else g.end
        self.canvas.set_time(t, glow_window=0.04 if self.play_timer.isActive() else 0.0)
        self.chart.set_at(v / SLIDER_MAX)
        c = g.counts_at(t)
        self.when.setText(("Now · " if v == SLIDER_MAX else "") + f"{t:%Y-%m-%d %H:%M}")
        days = max(0, (t - g.start).days)
        outdated = f" ({c['outdated']} outdated)" if c["outdated"] else ""
        self.summary.setText(f"{c['notes']} notes → {c['pages']} wiki pages with {c['facts']} facts{outdated} · "
                             f"{c['rules']} rules · {days} day{'s' if days != 1 else ''} since the first memory")
        self.activity.clear()
        def name(m):  # [[2026-11-24-1530-my-cat]] → “My cat”
            n = g.nodes.get(f"note:{m.group(1)}")
            return f"“{n.title}”" if n else m.group(1)

        for when, text in reversed([x for x in g.log if x[0] <= t][-200:]):
            self.activity.addItem(f"{when:%Y-%m-%d %H:%M}  {wb_brain_map.LINK_RE.sub(name, text)}")
        if not self.canvas.sel:
            self._show_node("")

    def toggle_play(self):
        if self.play_timer.isActive():
            self._stop()
            return
        if self.slider.value() >= SLIDER_MAX:
            self.slider.setValue(0)
        self.play_btn.setText("❚❚ Pause")
        self.play_timer.start()

    def _stop(self):
        self.play_timer.stop()
        self.play_btn.setText("▶ Play")
        self.canvas.recent = set()
        self.canvas.update()

    def _play_step(self):
        v = self.slider.value() + 4      # about ten seconds from start to now
        if v >= SLIDER_MAX:
            self.slider.setValue(SLIDER_MAX)
            self._stop()
        else:
            self.slider.setValue(v)

    # ---- filters and search -----------------------------------------------------------------
    def _filters(self):
        self.canvas.show_notes = self.notes_box.isChecked()
        self.canvas.show_rules = self.rules_box.isChecked()
        self.canvas._update_visible()
        self.canvas.update()

    def _find(self, text: str):
        q = text.strip().lower()
        g = self.graph
        self.canvas.match = {i for i, n in g.nodes.items()
                             if q and (q in n.title.lower() or q in n.text.lower() or
                                       any(q in f.text.lower() for f in n.facts))} if g else set()
        self.canvas.update()

    def _select_first_match(self):
        vis = {self.canvas.ids[k] for k in np.nonzero(self.canvas.visible)[0]}
        hits = sorted(self.canvas.match & vis, key=lambda i: (not i.startswith("page:"), i))
        if hits:
            self._select(hits[0])

    def _select(self, node_id: str):
        if self.graph and node_id in self.graph.nodes:
            self.canvas.sel = node_id
            self.canvas.update()
            self._show_node(node_id)

    def _open_node(self, node_id: str):
        n = self.graph.nodes.get(node_id) if self.graph else None
        if n and n.path and self.open_file:
            self.open_file(n.path)

    # ---- the side panel ---------------------------------------------------------------------
    def _show_node(self, node_id: str):
        g = self.graph
        if not g:
            return
        t = self.canvas.t
        e = html.escape
        n = g.nodes.get(node_id)
        if not n:
            c = g.counts_at(t)
            pages = sorted((x for x in g.nodes.values() if x.kind == "page" and x.t <= t),
                           key=lambda x: -len(x.active_facts(t)))[:8]
            top = "".join(f"<li><a href='{e(x.id)}' style='color:{GOLD}'>{e(x.title)}</a> · "
                          f"{len(x.active_facts(t))}</li>" for x in pages)
            self.details.setHtml(
                f"<h3 style='color:{GOLD}'>How the brain has grown</h3>"
                f"<p>Every note you or the assistant saves is filed by the local AI into wiki pages, and every "
                f"fact keeps the note it came from. Corrections you repeat become rules.</p>"
                f"<p>At {t:%Y-%m-%d %H:%M}: <b>{c['notes']}</b> notes, <b>{c['pages']}</b> pages, "
                f"<b>{c['facts']}</b> facts, <b>{c['outdated']}</b> outdated, <b>{c['rules']}</b> rules.</p>"
                + (f"<p style='color:{MUTED}'>Biggest pages</p><ol>{top}</ol>" if top else "")
                + f"<p style='color:{MUTED}'>Press Play to watch it grow from the first note.</p>")
            return
        links = sorted((g.nodes[x] for x in self.canvas.nbrs[node_id] if g.nodes[x].t <= t),
                       key=lambda x: (x.kind != "page", x.title.lower()))

        def link(x):
            col = {"page": GOLD, "note": NOTE_C.name(), "rule": RULE_C.name(), "hub": RULE_C.name()}[x.kind]
            return f"<a href='{e(x.id)}' style='color:{col}'>{e(x.title)}</a>"

        if n.kind == "page":
            rows = []
            for f in n.facts:
                if f.added > t:
                    continue
                src = ", ".join(link(g.nodes[f"note:{s}"]) if f"note:{s}" in g.nodes else e(s) for s in f.notes)
                if f.outdated and f.outdated <= t:
                    by = g.nodes.get(f"note:{f.outdated_by}")
                    rows.append(f"<li style='color:{MUTED}'><s>{e(f.text)}</s><br><small>outdated "
                                f"{f.outdated:%Y-%m-%d} by {link(by) if by else e(f.outdated_by)}</small></li>")
                else:
                    rows.append(f"<li>{e(f.text)}<br><small style='color:{MUTED}'>{f.added:%Y-%m-%d} · "
                                f"from {src}</small></li>")
            related = [x for x in links if x.kind == "page"]
            body = (f"<p style='color:{MUTED}'>Wiki page · since {n.t:%Y-%m-%d}</p><ul>{''.join(rows)}</ul>"
                    + (f"<p style='color:{MUTED}'>Related pages</p><p>{' · '.join(map(link, related))}</p>"
                       if related else ""))
        elif n.kind == "note":
            pages = [x for x in links if x.kind == "page"]
            body = (f"<p style='color:{MUTED}'>Note · {n.t:%Y-%m-%d %H:%M} · "
                    f"{'filed' if n.filed else 'not filed yet'}</p><p>{e(n.text[:1500])}</p>"
                    + (f"<p style='color:{MUTED}'>Filed into</p><p>{' · '.join(map(link, pages))}</p>"
                       if pages else ""))
        elif n.kind == "rule":
            body = (f"<p style='color:{MUTED}'>{'Learned from your corrections' if n.learned else 'Your own rule'}"
                    f" · {n.t:%Y-%m-%d}</p><p>{e(n.title)}</p>")
        else:
            body = "<ul>" + "".join(f"<li>{link(x)}</li>" for x in links) + "</ul>"
        self.details.setHtml(f"<h3 style='color:{GOLD}'>{e(n.title)}</h3>{body}"
                             f"<p style='color:{MUTED}'><small>Double-click the node to open its file.</small></p>")

    def closeEvent(self, ev):
        self.watch.stop()
        self.play_timer.stop()
        self.canvas.timer.stop()
        super().closeEvent(ev)
