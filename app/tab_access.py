"""Access tab: what the assistant may do: programs, status checks, containers, and the rules."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

import wb_access
from wb_common import run_in_background
from wb_settings import Settings

RULES = """<table cellspacing=6>
<tr><td style='color:#9be59b'><b>Free</b></td><td>open the programs below, check status, read your tasks, the TV; look at this PC
(name, IP, CPU, memory, free space) and list, find and read files in your user folder and code folders (read-only;
never hidden folders, AppData or key and password files)</td></tr>
<tr><td style='color:#d4af37'><b>Asks first</b></td><td>open any other program, add or finish a task, restart a container,
media keys (play/pause, next, volume). The question appears on screen; only a click answers it, never your voice,
so the TV or a visitor can't say yes. No answer in 90 s means no.</td></tr>
<tr><td style='color:#ff8a80'><b>Never</b></td><td>delete files, send email or messages, payments, router/NAS/Windows settings.
Programs that could do those (PowerShell, Terminal, Command Prompt, Registry Editor, Control Panel, Settings, WSL,
uninstallers, security tools) can't be opened by the assistant even with your permission.</td></tr></table>"""


class ProgramPicker(QDialog):
    def __init__(self, parent, apps: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Add programs")
        self.resize(420, 520)
        self.filter = QLineEdit(placeholderText="Search…")
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.apps = apps
        self.filter.textChanged.connect(self._fill)
        self._fill("")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.filter)
        lay.addWidget(self.list, 1)
        lay.addWidget(QLabel("Programs that are never allowed aren't listed."))
        lay.addWidget(buttons)

    def _fill(self, text: str):
        self.list.clear()
        self.list.addItems([a for a in self.apps if text.lower() in a.lower()])

    def chosen(self) -> list[str]:
        return [i.text() for i in self.list.selectedItems()]


class AccessTab(QWidget):
    def __init__(self, settings: Settings, status):
        super().__init__()
        self.s = settings
        self.status = status

        title = QLabel("Access")
        title.setObjectName("title")
        rules = QLabel(RULES)
        rules.setWordWrap(True)

        # Programs
        self.programs = QListWidget()
        add_prog = QPushButton("Add programs…")
        add_prog.clicked.connect(self.add_programs)
        rem_prog = QPushButton("Remove")
        rem_prog.clicked.connect(lambda: self._remove_list(self.programs, self.s.allowed_programs))
        prog_box = QGroupBox("Programs it may open without asking")
        p = QVBoxLayout(prog_box)
        p.addWidget(self.programs, 1)
        pr = QHBoxLayout()
        pr.addWidget(add_prog)
        pr.addWidget(rem_prog)
        pr.addStretch()
        p.addLayout(pr)
        p.addWidget(self._muted("Other programs: it asks you, and “Always allow” adds them here."))

        # Status checks
        self.targets = self._table(["Name", "Host", "Port"])
        add_t = QPushButton("Add")
        add_t.clicked.connect(lambda: self._add_row(self.targets, ["New service", "", ""]))
        rem_t = QPushButton("Remove")
        rem_t.clicked.connect(lambda: self._remove_rows(self.targets))
        check = QPushButton("Check now")
        check.clicked.connect(self.check_now)
        status_box = QGroupBox("Status checks (free, read-only)")
        sv = QVBoxLayout(status_box)
        sv.addWidget(self.targets, 1)
        sr = QHBoxLayout()
        for b in (add_t, rem_t, check):
            sr.addWidget(b)
        sr.addStretch()
        sv.addLayout(sr)

        # Containers
        self.containers = self._table(["Container", "Host (ssh name)"])
        load = QPushButton("Load from a host…")
        load.clicked.connect(self.load_containers)
        rem_c = QPushButton("Remove")
        rem_c.clicked.connect(lambda: self._remove_rows(self.containers))
        cont_box = QGroupBox("Containers it may restart (asks first)")
        cv = QVBoxLayout(cont_box)
        cv.addWidget(self.containers, 1)
        cr = QHBoxLayout()
        cr.addWidget(load)
        cr.addWidget(rem_c)
        cr.addStretch()
        cv.addLayout(cr)

        save = QPushButton("Save access")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        self.saved = QLabel("")
        self.saved.setObjectName("muted")
        bottom = QHBoxLayout()
        bottom.addWidget(self.saved, 1)
        bottom.addWidget(save)

        row = QHBoxLayout()
        row.addWidget(prog_box, 1)
        row.addWidget(status_box, 1)
        row.addWidget(cont_box, 1)
        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(rules)
        lay.addLayout(row, 1)
        lay.addLayout(bottom)
        self.load()

    # ---- helpers ------------------------------------------------------------------------
    @staticmethod
    def _muted(text):
        l = QLabel(text)
        l.setObjectName("muted")
        l.setWordWrap(True)
        return l

    @staticmethod
    def _table(headers):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        t.verticalHeader().hide()
        t.setSelectionBehavior(QTableWidget.SelectRows)
        return t

    @staticmethod
    def _add_row(table, values):
        r = table.rowCount()
        table.insertRow(r)
        for c, v in enumerate(values):
            table.setItem(r, c, QTableWidgetItem(str(v)))

    @staticmethod
    def _remove_rows(table):
        for r in sorted({i.row() for i in table.selectedIndexes()}, reverse=True):
            table.removeRow(r)

    def _remove_list(self, widget, values):
        for item in widget.selectedItems():
            if item.text() in values:
                values.remove(item.text())
        self.load_programs()

    @staticmethod
    def _rows(table):
        return [[(table.item(r, c).text().strip() if table.item(r, c) else "")
                 for c in range(table.columnCount())] for r in range(table.rowCount())]

    # ---- load / save --------------------------------------------------------------------
    def load(self):
        self.load_programs()
        self.targets.setRowCount(0)
        for t in self.s.status_targets:
            self._add_row(self.targets, [t["name"], t["host"], t["port"]])
        self.containers.setRowCount(0)
        for c in self.s.allowed_containers:
            self._add_row(self.containers, [c["name"], c["host"]])

    def load_programs(self):
        self.programs.clear()
        self.programs.addItems(sorted(self.s.allowed_programs, key=str.lower))

    def save(self):
        self.s.status_targets = [{"name": n, "host": h, "port": int(p)} for n, h, p in self._rows(self.targets)
                                 if n and h and p.isdigit()]
        self.s.allowed_containers = [{"name": n, "host": h} for n, h in self._rows(self.containers) if n and h]
        self.s.save()
        self.saved.setText(f"Saved: {len(self.s.allowed_programs)} programs, {len(self.s.status_targets)} "
                           f"status checks, {len(self.s.allowed_containers)} containers.")

    # ---- actions ------------------------------------------------------------------------
    def add_programs(self):
        def got(apps):
            names = sorted((n for n in apps if not wb_access.is_forbidden(n)), key=str.lower)
            dlg = ProgramPicker(self, names)
            if dlg.exec():
                for n in dlg.chosen():
                    if n not in self.s.allowed_programs:
                        self.s.allowed_programs.append(n)
                self.s.save()
                self.load_programs()

        run_in_background(lambda emit: wb_access.start_apps(refresh=True), on_result=got)

    def check_now(self):
        self.save()
        self.saved.setText("Checking…")
        run_in_background(lambda emit: wb_access.check_status(),
                          on_result=lambda r: self.saved.setText("  ·  ".join(
                              f"{k}: {v}" for k, v in __import__('json').loads(r).items())))

    def load_containers(self):
        host, ok = QInputDialog.getText(self, "Load containers", "SSH host name (as in ~/.ssh/config):", text="pi")
        if not ok or not host.strip():
            return
        host = host.strip()
        docker = "/usr/local/bin/docker" if host == "nas" else "docker"

        def got(out):
            existing = {(n, h) for n, h in self._rows(self.containers)}
            names = [n for n in out.split() if n]
            for n in names:
                if (n, host) not in existing:
                    self._add_row(self.containers, [n, host])
            self.saved.setText(f"Found {len(names)} containers on {host}. Remove the ones it shouldn't "
                               "restart, then Save access.")

        run_in_background(lambda emit: wb_access._ssh(host, f"{docker} ps --format '{{{{.Names}}}}'"),
                          on_result=got,
                          on_error=lambda e: QMessageBox.warning(self, "Load containers", e))
