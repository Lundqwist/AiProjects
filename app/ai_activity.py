"""AI activity: a window that shows, live, what the AI does for each job.

Left: the jobs (runs) since the workbench started. Middle: the selected job as a chain of steps
(wake word → speech to text → brain recall → model rounds → tools → speaking), and below it a
timeline that shows where the seconds went. Right: what went in and out of the selected step:
the text heard, the facts recalled, the model's tokens and speed, a tool's arguments and result.
Bottom: the machine: graphics card load, memory and temperature, and which models Ollama has loaded.
"""
from __future__ import annotations

import html
import json
import math
import subprocess
import time

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QScrollArea, QSplitter, QTextBrowser, QVBoxLayout, QWidget)

import wb_trace
from wb_common import GOLD, MUTED, NAVY, NAVY_2, NAVY_3, TEXT, run_in_background

OK_C, ERR_C = QColor("#9be59b"), QColor("#ff8a80")


def kind_label(kind: str) -> str:
    return wb_trace.KINDS.get(kind, (kind, ""))[0]


def kind_colour(kind: str) -> QColor:
    return QColor(wb_trace.KINDS.get(kind, ("", MUTED))[1] or MUTED)


def secs(x: float) -> str:
    return f"{x * 1000:.0f} ms" if x < 1 else f"{x:.1f} s" if x < 60 else f"{int(x // 60)} min {x % 60:.0f} s"


class _Bridge(QObject):
    changed = Signal(str, object)


# ---- the chain of steps ------------------------------------------------------------------------


class FlowView(QWidget):
    """The run's top-level steps as boxes joined by arrows, wrapping onto new rows."""
    picked = Signal(int)

    BOX_W, BOX_H, GAP_X, GAP_Y, PAD = 170, 74, 34, 26, 14

    def __init__(self):
        super().__init__()
        self.run: wb_trace.Run | None = None
        self.sel: int | None = None
        self.boxes: list[tuple[QRectF, wb_trace.Span]] = []
        self.setMinimumHeight(self.BOX_H + 2 * self.PAD)

    def set_run(self, run, sel):
        self.run, self.sel = run, sel
        self._layout()
        self.update()

    def _layout(self):
        self.boxes = []
        if not self.run:
            return
        top = [s for s in list(self.run.spans) if s.parent is None]
        per_row = max(1, (self.width() - 2 * self.PAD + self.GAP_X) // (self.BOX_W + self.GAP_X))
        for i, s in enumerate(top):
            row, col = divmod(i, per_row)
            if row % 2:  # a snake: every other row runs right to left, so arrows stay short
                col = per_row - 1 - col
            x = self.PAD + col * (self.BOX_W + self.GAP_X)
            y = self.PAD + row * (self.BOX_H + self.GAP_Y)
            self.boxes.append((QRectF(x, y, self.BOX_W, self.BOX_H), s))
        rows = math.ceil(len(top) / per_row) if top else 1
        h = 2 * self.PAD + rows * self.BOX_H + (rows - 1) * self.GAP_Y
        if self.minimumHeight() != h:
            self.setMinimumHeight(h)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._layout()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(NAVY))
        if not self.run:
            p.setPen(QColor(MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "No AI work yet. Ask the voice assistant or an expert something,\n"
                                                    "and each step it takes shows up here as it happens.")
            return
        if not self.boxes:
            p.setPen(QColor(MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Starting…")
            return
        # arrows first, under the boxes
        p.setPen(QPen(QColor(NAVY_3).lighter(160), 2))
        for (a, _), (b, _) in zip(self.boxes, self.boxes[1:]):
            if abs(a.center().y() - b.center().y()) < 1:
                start = QPointF(a.right(), a.center().y()) if b.x() > a.x() else QPointF(a.left(), a.center().y())
                end = QPointF(b.left(), b.center().y()) if b.x() > a.x() else QPointF(b.right(), b.center().y())
            else:
                start, end = QPointF(a.center().x(), a.bottom()), QPointF(b.center().x(), b.top())
            p.drawLine(start, end)
            ang = math.atan2(end.y() - start.y(), end.x() - start.x())
            for d in (2.6, -2.6):
                p.drawLine(end, QPointF(end.x() - 8 * math.cos(ang + d / 6), end.y() - 8 * math.sin(ang + d / 6)))
        pulse = 0.5 + 0.5 * math.sin(time.time() * 5)
        small, bold, plain = QFont("Segoe UI", 8), QFont("Segoe UI", 9, QFont.DemiBold), QFont("Segoe UI", 8)
        kids = {}
        for s in self.run.spans:
            if s.parent is not None:
                kids[s.parent] = kids.get(s.parent, 0) + 1
        for r, s in self.boxes:
            c = kind_colour(s.kind)
            fill = QColor(NAVY_2)
            p.setBrush(fill)
            width = 2.5 if s.id == self.sel else 1.4
            border = QColor(c)
            if s.status == "running":
                border.setAlpha(int(110 + 145 * pulse))
                width = 2.5
            elif s.status == "error":
                border = ERR_C
            p.setPen(QPen(border, width))
            p.drawRoundedRect(r, 8, 8)
            if s.status == "running":  # a bar that fills while it runs
                p.setPen(Qt.NoPen)
                glow = QColor(c)
                glow.setAlpha(60)
                p.setBrush(glow)
                frac = (time.time() * 0.6) % 1
                p.drawRoundedRect(QRectF(r.x() + 2, r.bottom() - 6, (r.width() - 4) * frac, 4), 2, 2)
            p.setPen(c)
            p.setFont(small)
            p.drawText(QRectF(r.x() + 10, r.y() + 6, r.width() - 20, 14), Qt.AlignLeft, kind_label(s.kind).upper())
            p.setPen(QColor(TEXT))
            p.setFont(bold)
            name = QFontMetrics(bold).elidedText(s.name, Qt.ElideRight, int(r.width() - 20))
            p.drawText(QRectF(r.x() + 10, r.y() + 22, r.width() - 20, 18), Qt.AlignLeft, name)
            p.setFont(plain)
            p.setPen(QColor(MUTED) if s.status != "error" else ERR_C)
            info = "running… " + secs(s.duration) if s.status == "running" else \
                ("failed · " if s.status == "error" else "") + (secs(s.duration) if s.end and s.duration > 0.0005 else "")
            tps = s.detail.get("tokens_per_s")
            if tps and s.status != "running":
                info += f" · {tps:.0f} tok/s"
            if kids.get(s.id):
                info += f" · {kids[s.id]} inside"
            p.drawText(QRectF(r.x() + 10, r.y() + 44, r.width() - 20, 18), Qt.AlignLeft,
                       QFontMetrics(plain).elidedText(info, Qt.ElideRight, int(r.width() - 20)))

    def mousePressEvent(self, ev):
        for r, s in self.boxes:
            if r.contains(ev.position()):
                self.picked.emit(s.id)
                return


# ---- where the time went -----------------------------------------------------------------------


class Waterfall(QWidget):
    """Every step, nested steps indented, as a bar over the run's time."""
    picked = Signal(int)
    ROW, LABEL_W, TOP = 22, 280, 24

    def __init__(self):
        super().__init__()
        self.run: wb_trace.Run | None = None
        self.sel: int | None = None

    def set_run(self, run, sel):
        self.run, self.sel = run, sel
        n = len(run.spans) if run else 0
        self.setMinimumHeight(self.TOP + max(1, n) * self.ROW + 8)
        self.update()

    def _ordered(self):
        spans = list(self.run.spans)
        by_parent: dict = {}
        for s in spans:
            by_parent.setdefault(s.parent, []).append(s)
        out = []

        def walk(parent, depth):
            for s in by_parent.get(parent, []):
                out.append((s, depth))
                walk(s.id, depth + 1)
        walk(None, 0)
        return out

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(NAVY))
        if not self.run or not self.run.spans:
            return
        t0, t1 = self.run.start, self.run.end or time.time()
        span_t = max(t1 - t0, 0.001)
        x0, w = self.LABEL_W, self.width() - self.LABEL_W - 70
        p.setFont(QFont("Segoe UI", 8))
        # a time scale
        p.setPen(QColor(MUTED))
        step = next(s for s in (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1e9) if span_t / s <= 8)
        k = 0
        while k * step <= span_t:
            x = x0 + w * (k * step) / span_t
            p.setPen(QPen(QColor(NAVY_3), 1))
            p.drawLine(QPointF(x, self.TOP - 4), QPointF(x, self.height()))
            p.setPen(QColor(MUTED))
            p.drawText(QPointF(x + 2, 12), secs(k * step) if k else "0")
            k += 1
        fm = QFontMetrics(p.font())
        for row, (s, depth) in enumerate(self._ordered()):
            y = self.TOP + row * self.ROW
            if s.id == self.sel:
                p.fillRect(QRectF(0, y, self.width(), self.ROW), QColor(NAVY_2))
            c = kind_colour(s.kind)
            p.setPen(c)
            label = ("   " * depth) + f"{kind_label(s.kind)}: {s.name}"
            p.drawText(QRectF(8, y, self.LABEL_W - 14, self.ROW), Qt.AlignVCenter | Qt.AlignLeft,
                       fm.elidedText(label, Qt.ElideRight, self.LABEL_W - 16))
            a = x0 + w * (s.start - t0) / span_t
            b = x0 + w * ((s.end or time.time()) - t0) / span_t
            bar = QColor(c if s.status != "error" else ERR_C)
            bar.setAlpha(200 if s.status != "running" else int(120 + 100 * (0.5 + 0.5 * math.sin(time.time() * 5))))
            p.setPen(Qt.NoPen)
            p.setBrush(bar)
            p.drawRoundedRect(QRectF(a, y + 5, max(3.0, b - a), self.ROW - 10), 3, 3)
            p.setPen(QColor(TEXT))
            p.drawText(QPointF(max(a, b) + 6, y + self.ROW / 2 + 4),
                       secs(s.duration) + (" …" if s.status == "running" else ""))

    def mousePressEvent(self, ev):
        if not self.run:
            return
        row = int((ev.position().y() - self.TOP) // self.ROW)
        ordered = self._ordered()
        if 0 <= row < len(ordered):
            self.picked.emit(ordered[row][0].id)


# ---- the window --------------------------------------------------------------------------------


def gpu_and_models() -> dict:
    """Graphics card use (nvidia-smi) and the models Ollama has loaded. Runs in a worker thread."""
    out: dict = {}
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        name, util, used, total, temp = [x.strip() for x in r.stdout.splitlines()[0].split(",")]
        out["gpu"] = {"name": name, "util": float(util), "used": float(used), "total": float(total),
                      "temp": float(temp)}
    except (OSError, IndexError, ValueError, subprocess.SubprocessError):
        pass
    try:
        import ollama
        out["models"] = [{"name": m.model, "vram": (m.size_vram or 0) / 2**30, "size": (m.size or 0) / 2**30}
                         for m in ollama.ps().models]
    except Exception:  # noqa: BLE001 - Ollama not running
        out["models"] = None
    return out


class AiActivityWindow(QDialog):
    def __init__(self, parent=None, show_on_map=None):
        super().__init__(parent)
        self.setWindowTitle("AI activity · AI Workbench")
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.resize(1300, 820)
        self.show_on_map = show_on_map
        self.run_id: int | None = None
        self.span_id: int | None = None
        self.machine_busy = False

        title = QLabel("AI activity")
        title.setObjectName("title")
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        self.follow = QCheckBox("Follow live")
        self.follow.setChecked(True)
        self.follow.setToolTip("Show each new job as soon as it starts")
        clear = QPushButton("Clear")
        clear.setToolTip("Forget the finished jobs (nothing is kept on disk anyway)")
        clear.clicked.connect(lambda: (wb_trace.clear(), self.refresh()))
        top = QHBoxLayout()
        top.addWidget(title)
        top.addSpacing(12)
        top.addWidget(self.summary, 1)
        top.addWidget(self.follow)
        top.addWidget(clear)

        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setSpacing(2)
        self.list.itemClicked.connect(lambda it: self._pick_run(it.data(Qt.UserRole), manual=True))
        self.flow = FlowView()
        self.flow.picked.connect(self._pick_span)
        self.water = Waterfall()
        self.water.picked.connect(self._pick_span)
        flow_box = QWidget()
        fl = QVBoxLayout(flow_box)
        fl.setContentsMargins(0, 0, 0, 0)
        steps_label = QLabel("Steps")
        steps_label.setObjectName("muted")
        time_label = QLabel("Where the time went")
        time_label.setObjectName("muted")
        water_scroll = QScrollArea()
        water_scroll.setWidgetResizable(True)
        water_scroll.setWidget(self.water)
        flow_scroll = QScrollArea()
        flow_scroll.setWidgetResizable(True)
        flow_scroll.setWidget(self.flow)
        fl.addWidget(steps_label)
        fl.addWidget(flow_scroll, 1)
        fl.addWidget(time_label)
        fl.addWidget(water_scroll, 1)
        self.details = QTextBrowser()
        self.details.setOpenLinks(False)
        self.details.anchorClicked.connect(self._link)
        split = QSplitter()
        split.addWidget(self.list)
        split.addWidget(flow_box)
        split.addWidget(self.details)
        split.setSizes([260, 700, 340])
        split.setStretchFactor(1, 1)

        self.machine = QLabel("Reading the graphics card…")
        self.machine.setObjectName("muted")
        self.machine.setTextFormat(Qt.RichText)
        legend = QLabel(" ".join(f"<span style='color:{c}'>■</span> {html.escape(l)} &nbsp;"
                                 for k, (l, c) in wb_trace.KINDS.items() if k != "step"))
        legend.setObjectName("muted")

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(split, 1)
        lay.addWidget(self.machine)
        lay.addWidget(legend)

        self.bridge = _Bridge()
        self.bridge.changed.connect(self._changed)
        self._listener = lambda ev, obj: self.bridge.changed.emit(ev, obj)
        wb_trace.subscribe(self._listener)
        self.pending = False
        self.anim = QTimer(self, interval=120, timeout=self._animate)
        self.anim.start()
        self.machine_timer = QTimer(self, interval=2000, timeout=self.read_machine)
        self.machine_timer.start()
        self.read_machine()
        runs = wb_trace.runs()
        if runs:
            self.run_id = runs[-1].id
        self.refresh()

    # ---- updates ----------------------------------------------------------------------------
    def _changed(self, ev, obj):
        if ev == "run" and isinstance(obj, wb_trace.Run) and obj.status == "running" and self.follow.isChecked():
            self.run_id, self.span_id = obj.id, None
        if ev == "span" and isinstance(obj, wb_trace.Span) and self.follow.isChecked() and obj.run.id == self.run_id \
                and obj.status == "running" and obj.parent is None:
            self.span_id = obj.id  # follow the step that is running now
        if not self.pending:  # many changes at once (a streamed answer): redraw once
            self.pending = True
            QTimer.singleShot(80, self.refresh)

    def _animate(self):
        if any(r.status == "running" for r in wb_trace.runs()):
            self.flow.update()
            self.water.update()
            self.ticks = getattr(self, "ticks", 0) + 1
            run = self.current_run()
            if run and run.status == "running" and self.ticks % 4 == 0:  # running time and a growing answer
                self._show_details()

    def refresh(self):
        self.pending = False
        runs = wb_trace.runs()
        self.list.blockSignals(True)
        self.list.clear()
        for r in reversed(runs):
            mark = {"running": "◉", "ok": "✓", "error": "✗"}[r.status]
            it = QListWidgetItem(f"{mark} {time.strftime('%H:%M:%S', time.localtime(r.start))}  {r.title}\n"
                                 f"    {len(r.spans)} steps · {secs(r.duration)}")
            it.setData(Qt.UserRole, r.id)
            it.setForeground(QColor(GOLD) if r.status == "running" else ERR_C if r.status == "error" else QColor(TEXT))
            self.list.addItem(it)
            if r.id == self.run_id:
                it.setSelected(True)
        self.list.blockSignals(False)
        run = self.current_run()
        self.flow.set_run(run, self.span_id)
        self.water.set_run(run, self.span_id)
        done = [r for r in runs if r.status != "running"]
        models = {s.name for r in runs for s in r.spans if s.kind == "model"}
        online = sum(1 for r in runs for s in r.spans if s.kind == "online" and s.status == "ok")
        tools = sum(1 for r in runs for s in r.spans if s.kind == "tool")
        self.summary.setText(f"{len(runs)} jobs since start ({len(runs) - len(done)} running) · "
                             f"{len(models)} local model{'s' if len(models) != 1 else ''} used · {tools} tool calls · "
                             f"{online} answered online · nothing here is saved or sent anywhere")
        self._show_details()

    def current_run(self) -> wb_trace.Run | None:
        return next((r for r in wb_trace.runs() if r.id == self.run_id), None)

    def _pick_run(self, run_id, manual=False):
        self.run_id, self.span_id = run_id, None
        if manual:
            run = self.current_run()
            self.follow.setChecked(bool(run and run.status == "running"))
        self.refresh()

    def _pick_span(self, span_id):
        self.span_id = None if span_id == self.span_id else span_id
        self.follow.setChecked(False)  # looking at a step: don't jump away from it
        self.refresh()

    # ---- the side panel ---------------------------------------------------------------------
    def _show_details(self):
        run = self.current_run()
        e = html.escape
        if not run:
            self.details.setHtml(
                f"<h3 style='color:{GOLD}'>What happens when the AI works</h3>"
                "<p>Every job is a chain of steps. A spoken question, for example:</p><ol>"
                "<li><b>Wake word</b>: a tiny Whisper model on the CPU listens for “Hey Luna”.</li>"
                "<li><b>Speech to text</b>: a bigger Whisper model writes down what you said.</li>"
                "<li><b>Brain recall</b>: your notes are searched by meaning (embeddings) and the closest facts "
                "are added to the instructions.</li>"
                "<li><b>Model</b>: the language model reads everything and either answers or asks for a "
                "<b>tool</b> (TV, tasks, web search, an expert…). Each tool result goes back to the model for "
                "another round.</li>"
                "<li><b>Speaking</b>: Piper turns the answer into speech.</li></ol>"
                "<p>Click a job on the left, then a step, to see what went in and came out.</p>")
            return
        span = next((s for s in run.spans if s.id == self.span_id), None)
        if not span:
            total = {}
            for s in run.spans:
                if s.parent is None:
                    total[s.kind] = total.get(s.kind, 0) + s.duration
            rows = "".join(f"<tr><td style='color:{kind_colour(k).name()}'>{e(kind_label(k))}</td>"
                           f"<td align='right'>&nbsp; {secs(v)}</td><td align='right'>&nbsp; "
                           f"{v / max(run.duration, 1e-6):.0%}</td></tr>"
                           for k, v in sorted(total.items(), key=lambda kv: -kv[1]))
            toks = sum(int(s.detail.get("tokens_out") or 0) for s in run.spans if s.kind in ("model", "online"))
            self.details.setHtml(
                f"<h3 style='color:{GOLD}'>{e(run.title)}</h3>"
                f"<p style='color:{MUTED}'>{time.strftime('%H:%M:%S', time.localtime(run.start))} · "
                f"{secs(run.duration)} · {len(run.spans)} steps · {toks} tokens written"
                f"{' · <span style=color:#ff8a80>' + e(run.error) + '</span>' if run.error else ''}</p>"
                f"<p>Time per kind of step:</p><table>{rows}</table>"
                f"<p style='color:{MUTED}'>Click a step for what went in and came out.</p>")
            return
        d = dict(span.detail)
        parts = [f"<h3 style='color:{kind_colour(span.kind).name()}'>{e(kind_label(span.kind))}: {e(span.name)}</h3>",
                 f"<p style='color:{MUTED}'>{'running for ' if span.status == 'running' else ''}{secs(span.duration)}"
                 f"{' · failed: ' + e(span.error) if span.error else ''}</p>"]
        stats = [(k, d.pop(k)) for k in ("tokens_in", "tokens_out", "tokens_per_s", "reading_s", "loading_s")
                 if k in d]
        if stats:
            names = {"tokens_in": "tokens read", "tokens_out": "tokens written", "tokens_per_s": "tokens per second",
                     "reading_s": "seconds reading the prompt", "loading_s": "seconds loading the model"}
            parts.append("<table>" + "".join(f"<tr><td style='color:{MUTED}'>{names[k]}</td>"
                                             f"<td align='right'>&nbsp; {v}</td></tr>" for k, v in stats) + "</table>")
        sources = d.pop("sources", None)
        for k, v in d.items():
            label = k.replace("_", " ")
            if isinstance(v, (list, tuple)):
                body = "<ul>" + "".join(f"<li>{e(str(x))}</li>" for x in v) + "</ul>" if v else "<p>(none)</p>"
            elif isinstance(v, dict):
                body = f"<pre>{e(json.dumps(v, ensure_ascii=False, indent=1))}</pre>"
            else:
                body = f"<p style='white-space:pre-wrap'>{e(str(v))}</p>"
            parts.append(f"<p style='color:{MUTED}'><b>{e(label)}</b></p>{body}")
        if sources and self.show_on_map:
            parts.append(f"<p><a href='map:{e(json.dumps(sources))}' style='color:{GOLD}'>"
                         "Show these on the Brain map</a></p>")
        kids = [s for s in run.spans if s.parent == span.id]
        if kids:
            parts.append(f"<p style='color:{MUTED}'><b>Steps inside</b></p><ul>" + "".join(
                f"<li><a href='span:{s.id}' style='color:{kind_colour(s.kind).name()}'>{e(kind_label(s.kind))}: "
                f"{e(s.name)}</a> · {secs(s.duration)}</li>" for s in kids) + "</ul>")
        bar = self.details.verticalScrollBar().value()
        self.details.setHtml("".join(parts))
        self.details.verticalScrollBar().setValue(bar)

    def _link(self, url):
        u = url.toString()
        if u.startswith("span:"):
            self._pick_span(int(u[5:]))
        elif u.startswith("map:") and self.show_on_map:
            self.show_on_map(json.loads(u[4:]))

    # ---- the machine ------------------------------------------------------------------------
    def read_machine(self):
        if self.machine_busy:
            return
        self.machine_busy = True

        def show(m):
            parts = []
            g = m.get("gpu")
            if g:
                col = ERR_C.name() if g["temp"] >= 83 else GOLD if g["util"] >= 50 else OK_C.name()
                parts.append(f"<span style='color:{col}'>●</span> {e(g['name'])}: {g['util']:.0f}% busy · "
                             f"memory {g['used'] / 1024:.1f} of {g['total'] / 1024:.1f} GB · {g['temp']:.0f} °C")
            models = m.get("models")
            if models is None:
                parts.append(f"<span style='color:{ERR_C.name()}'>Ollama is not running</span>")
            elif not models:
                parts.append("Ollama: no model loaded (the next question loads one, which takes a few seconds)")
            else:
                parts.append("Loaded in Ollama: " + ", ".join(
                    f"<b>{e(x['name'])}</b> {x['vram']:.1f} GB on the card"
                    + (f" + {x['size'] - x['vram']:.1f} GB in RAM" if x["size"] - x["vram"] > 0.1 else "")
                    for x in models))
            last = next((s for r in reversed(wb_trace.runs()) for s in reversed(r.spans)
                         if s.kind == "model" and s.detail.get("tokens_per_s")), None)
            if last:
                parts.append(f"last answer {last.detail['tokens_per_s']:.0f} tokens/s ({e(last.name)})")
            self.machine.setText(" &nbsp;·&nbsp; ".join(parts))

        e = html.escape
        run_in_background(lambda emit: gpu_and_models(), on_result=show, on_error=lambda _: None,
                          on_done=lambda: setattr(self, "machine_busy", False))

    def closeEvent(self, ev):
        wb_trace.unsubscribe(self._listener)
        self.anim.stop()
        self.machine_timer.stop()
        super().closeEvent(ev)

    def done(self, r):
        wb_trace.unsubscribe(self._listener)
        super().done(r)
