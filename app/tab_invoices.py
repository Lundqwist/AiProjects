"""Invoices tab: add invoices, process the inbox (once or by watching it), see results and reviews."""
from __future__ import annotations

import csv
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

import invoice
from wb_common import open_path, run_in_background
from wb_settings import Settings

SUFFIXES = {".pdf"} | invoice.IMAGE_SUFFIXES
COLUMNS = ["file", "supplier", "invoice_number", "invoice_date", "due_date", "subtotal", "vat_rate",
           "vat_amount", "total", "currency"]
HEADERS = ["File", "Supplier", "Number", "Date", "Due", "Excl. VAT", "VAT %", "VAT", "Total", "Cur."]


class _Inbox(FileSystemEventHandler):
    def __init__(self, callback):
        self.callback = callback

    def on_created(self, event):
        if not event.is_directory:
            self.callback(event.src_path)


class InvoicesTab(QWidget):
    def __init__(self, settings: Settings, status):
        super().__init__()
        self.s = settings
        self.status = status
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)  # one invoice at a time: one model on the GPU
        self.queued: set[str] = set()
        self.observer = None
        self.setAcceptDrops(True)

        add = QPushButton("Add invoices…")
        add.clicked.connect(self.add_files)
        self.process_btn = QPushButton("Process inbox now")
        self.process_btn.setObjectName("primary")
        self.process_btn.clicked.connect(self.process_inbox)
        self.watch = QCheckBox("Watch the inbox and process new files automatically")
        self.watch.toggled.connect(self.set_watching)
        open_inbox = QPushButton("Open inbox")
        open_inbox.clicked.connect(lambda: open_path(self.s.invoice_inbox))
        open_out = QPushButton("Open output")
        open_out.clicked.connect(lambda: open_path(self.s.invoice_output))
        open_csv = QPushButton("Open CSV in Excel")
        open_csv.clicked.connect(lambda: open_path(Path(self.s.invoice_output) / "invoices.csv"))
        top = QHBoxLayout()
        for w in (add, self.process_btn, open_inbox, open_out, open_csv):
            top.addWidget(w)
        top.addStretch()

        self.inbox_label = QLabel()
        self.inbox_label.setObjectName("muted")

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.doubleClicked.connect(self.open_processed)

        self.review = QListWidget()
        self.review.currentItemChanged.connect(self.show_review)
        self.review.doubleClicked.connect(lambda: self.review.currentItem() and open_path(
            self.review.currentItem().data(Qt.UserRole)))
        self.review_text = QPlainTextEdit(readOnly=True)
        retry = QPushButton("Move back to inbox and retry")
        retry.clicked.connect(self.retry_review)

        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumBlockCount(500)

        review_box = QWidget()
        rv = QVBoxLayout(review_box)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("Needs review (double-click to open the invoice)"))
        rv.addWidget(self.review, 1)
        rv.addWidget(self.review_text, 1)
        rv.addWidget(retry)

        upper = QWidget()
        up = QVBoxLayout(upper)
        up.setContentsMargins(0, 0, 0, 0)
        up.addWidget(QLabel("Processed invoices (double-click to open)"))
        up.addWidget(self.table)

        bottom = QSplitter()
        bottom.addWidget(review_box)
        log_box = QWidget()
        lb = QVBoxLayout(log_box)
        lb.setContentsMargins(0, 0, 0, 0)
        lb.addWidget(QLabel("Log"))
        lb.addWidget(self.log)
        bottom.addWidget(log_box)

        split = QSplitter(Qt.Vertical)
        split.addWidget(upper)
        split.addWidget(bottom)
        split.setSizes([400, 300])

        title = QLabel("Invoices")
        title.setObjectName("title")
        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(QLabel("Invoices (PDF or photo) are read by the vision model, checked "
                             "(lines, VAT and total must add up) and added to a CSV. "
                             "Ones that don't add up go to review."))
        lay.addLayout(top)
        lay.addWidget(self.watch)
        lay.addWidget(self.inbox_label)
        lay.addWidget(split, 1)

        self.timer = QTimer(self, interval=3000, timeout=self.refresh_inbox_count)
        self.timer.start()
        self.refresh()

    # ---- files --------------------------------------------------------------------------
    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add invoices", "",
                                                "Invoices (*.pdf *.png *.jpg *.jpeg *.webp)")
        self.copy_to_inbox([Path(f) for f in files])

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.copy_to_inbox([Path(u.toLocalFile()) for u in e.mimeData().urls()])

    def copy_to_inbox(self, files: list[Path]):
        inbox = Path(self.s.invoice_inbox)
        inbox.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in files:
            if f.suffix.lower() in SUFFIXES and f.is_file():
                shutil.copy2(f, inbox / f.name)  # copies: the original stays where it was
                n += 1
        self.status(f"Copied {n} invoice(s) to the inbox")
        self.refresh_inbox_count()
        if n and not self.watch.isChecked():
            self.process_inbox()

    # ---- processing ---------------------------------------------------------------------
    def process_inbox(self):
        inbox = Path(self.s.invoice_inbox)
        for f in sorted(inbox.glob("*")):
            self.enqueue(str(f))

    def enqueue(self, path: str):
        p = Path(path)
        if p.suffix.lower() not in SUFFIXES or path in self.queued:
            return
        self.queued.add(path)
        out = Path(self.s.invoice_output)

        def work(emit):
            import time
            time.sleep(0.5)  # a file that is still being copied
            if not p.exists():
                return None
            emit(f"Reading {p.name}…")
            return p.name, *invoice.process(p, out)

        def done(result):
            if result:
                name, ok, msg = result
                self.log.appendPlainText(f"{'OK    ' if ok else 'REVIEW'}  {name}: {msg}")
            self.refresh()

        def finished():
            self.queued.discard(path)

        run_in_background(work, on_progress=self.log.appendPlainText, on_result=done,
                          on_error=lambda e: self.log.appendPlainText(f"ERROR  {p.name}: {e}"),
                          on_done=finished, pool=self.pool)

    def set_watching(self, on: bool):
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=2)
            self.observer = None
        if on:
            inbox = Path(self.s.invoice_inbox)
            inbox.mkdir(parents=True, exist_ok=True)
            self.observer = Observer()
            # watchdog calls from its own thread; hop to the GUI thread before queueing
            self.observer.schedule(_Inbox(lambda p: QTimer.singleShot(0, self, lambda: self.enqueue(p))),
                                   str(inbox))
            self.observer.start()
            self.process_inbox()
            self.log.appendPlainText(f"Watching {inbox}")
        self.refresh_inbox_count()

    def shutdown(self):
        self.set_watching(False)

    # ---- display ------------------------------------------------------------------------
    def refresh_inbox_count(self):
        inbox = Path(self.s.invoice_inbox)
        n = sum(1 for f in inbox.glob("*") if f.suffix.lower() in SUFFIXES) if inbox.exists() else 0
        state = "watching" if self.observer else "not watching"
        self.inbox_label.setText(f"Inbox: {inbox}  ·  {n} waiting  ·  {state}")

    def refresh(self):
        out = Path(self.s.invoice_output)
        csv_path = out / "invoices.csv"
        rows = []
        if csv_path.exists():
            with csv_path.open(encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
        self.table.setRowCount(len(rows))
        for r, row in enumerate(reversed(rows)):  # newest first
            for c, key in enumerate(COLUMNS):
                item = QTableWidgetItem(str(row.get(key, "")))
                if key in ("subtotal", "vat_amount", "total", "vat_rate"):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, item)

        self.review.clear()
        review = out / "needs-review"
        for f in sorted(review.glob("*")) if review.exists() else []:
            if f.suffix.lower() in SUFFIXES:
                item = QListWidgetItem(f.name)
                item.setData(Qt.UserRole, str(f))
                self.review.addItem(item)
        self.review_text.clear()
        self.refresh_inbox_count()

    def show_review(self, item):
        if not item:
            return
        note = Path(item.data(Qt.UserRole) + ".txt")
        self.review_text.setPlainText(note.read_text(encoding="utf-8") if note.exists() else "")

    def retry_review(self):
        item = self.review.currentItem()
        if not item:
            return
        f = Path(item.data(Qt.UserRole))
        Path(str(f) + ".txt").unlink(missing_ok=True)
        dest = Path(self.s.invoice_inbox) / f.name
        shutil.move(f, dest)
        self.refresh()
        if not self.watch.isChecked():
            self.enqueue(str(dest))

    def open_processed(self, index):
        name = self.table.item(index.row(), 0).text()
        open_path(Path(self.s.invoice_output) / "processed" / name)
