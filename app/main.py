"""AI Workbench: one Windows program for the five local AI projects.

Run: .venv\\Scripts\\pythonw.exe app\\main.py   (or the Start menu shortcut)

Starting is done in two steps, so it never looks as if nothing happened:
  1. Only the light parts load, a loading window shows at once, and a check makes sure only one copy
     runs: starting the program again brings the open copy to the front instead.
  2. The heavy parts (chromadb, Whisper, the tabs: minutes after a reboot, a few seconds otherwise)
     load behind the loading window, then the main window (wb_window.py) replaces it.
"""
from __future__ import annotations

import hashlib
import importlib
import os
import sys
import threading
import time
import traceback
import warnings
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
sys.path[:0] = [str(APP_DIR)] + [str(ROOT / d) for d in ("01-rag", "02-voice", "03-invoices", "04-research")]
warnings.filterwarnings("ignore")

from wb_settings import SETTINGS_DIR, SETTINGS_FILE, Settings  # noqa: E402
import wb_lifecycle  # noqa: E402

settings = Settings.load()
os.environ["OLLAMA_HOST"] = settings.ollama_host  # must be set before ollama is imported

# pythonw has no console: send stray output and errors to a log file
LOG = SETTINGS_DIR / "workbench.log"
if sys.stdout is None or sys.stderr is None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOG, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = sys.stderr = log

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication, QIcon, QPixmap  # noqa: E402
from PySide6.QtNetwork import QLocalServer, QLocalSocket  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QMessageBox, QVBoxLayout, QWidget  # noqa: E402

from wb_common import GOLD, MUTED, NAVY, NAVY_3, THEME  # noqa: E402

# The plain-Python projects behind the tabs hold nearly all the start-up time (chromadb above all).
# They have no Qt in them, so they load on a background thread while the loading window stays alive.
HEAVY = ("rag", "voice", "agent", "invoice")
# Then the tabs, one by one on the main thread (Qt classes), with the loading window updated between them
STEPS = (("Brain", "tab_brain"), ("Documents", "tab_documents"), ("Create", "tab_create"), ("Code", "tab_code"),
         ("the conversation", "tab_chat"), ("Invoices", "tab_invoices"), ("Research", "tab_research"),
         ("My TV", "tab_tv"), ("Access", "tab_access"), ("Evaluation", "tab_eval"), ("Settings", "tab_settings"),
         ("the VS Code bridge", "wb_bridge"), ("updates", "wb_updates"), ("main window", "wb_window"))


def __getattr__(name):
    """make_screenshots.py uses main.MainWindow: load it from wb_window when asked for."""
    if name == "MainWindow":
        return importlib.import_module("wb_window").MainWindow
    raise AttributeError(name)


def instance_name() -> str:
    """One name per Windows user and settings file, so a demo or test copy with its own settings may
    run beside the real one."""
    key = f"{os.environ.get('USERNAME', '')}|{SETTINGS_FILE.resolve()}"
    return "Lundqwist.AIWorkbench-" + hashlib.sha1(key.lower().encode()).hexdigest()[:12]


def tell_running_copy(name: str) -> bool:
    """If AI Workbench already runs, ask it to come to the front and return True."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(1500):
        return False
    try:  # this process was just started by the user, so it may hand the foreground to the other one
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(0xFFFFFFFF)  # ASFW_ANY
    except (AttributeError, OSError):
        pass
    sock.write(b"show\n")
    sock.flush()
    sock.waitForBytesWritten(1500)
    sock.disconnectFromServer()
    return True


class LoadingWindow(QWidget):
    """A small navy and gold window that shows while the heavy parts load."""

    def __init__(self):
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint)
        self.setWindowTitle("AI Workbench")  # also its taskbar button, so you see it is starting
        self.setFixedSize(460, 270)
        frame = QFrame(self)
        frame.setObjectName("loadingFrame")
        frame.setGeometry(0, 0, 460, 270)
        frame.setStyleSheet(f"QFrame#loadingFrame {{ background: {NAVY}; border: 2px solid {GOLD}; border-radius: 10px; }}"
                            "QLabel { background: transparent; border: none; }")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(28, 22, 28, 20)
        lay.setSpacing(6)
        logo = QLabel(alignment=Qt.AlignCenter)
        pix = QPixmap(str(APP_DIR / "assets" / "lundqwist-logo.png"))
        if not pix.isNull():
            logo.setPixmap(pix.scaledToHeight(84, Qt.SmoothTransformation))
        lay.addWidget(logo)
        title = QLabel("AI Workbench", alignment=Qt.AlignCenter)
        title.setStyleSheet(f"color: {GOLD}; font-size: 18pt; font-weight: 600;")
        lay.addWidget(title)
        self.status = QLabel("Starting…", alignment=Qt.AlignCenter)
        self.status.setStyleSheet("font-size: 10.5pt;")
        lay.addWidget(self.status)
        self.hint = QLabel("", alignment=Qt.AlignCenter, wordWrap=True)
        self.hint.setStyleSheet(f"color: {MUTED}; font-size: 9pt;")
        lay.addWidget(self.hint)
        lay.addStretch(1)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {NAVY_3};")
        lay.addWidget(line)
        foot = QLabel("Local AI with Ollama  ·  lundqwist.se", alignment=Qt.AlignCenter)
        foot.setStyleSheet(f"color: {MUTED}; font-size: 8.5pt;")
        lay.addWidget(foot)
        self.started = time.monotonic()
        self.step = "Starting…"
        screen = QGuiApplication.primaryScreen()
        if screen:
            self.move(screen.availableGeometry().center() - self.rect().center())

    def show_step(self, text: str):
        self.step = text
        self.tick()

    def tick(self):
        secs = int(time.monotonic() - self.started)
        self.status.setText(self.step if secs < 3 else f"{self.step}   ({secs} s)")
        if secs >= 15:
            self.hint.setText("The first start after a restart of the PC can take a few minutes, "
                              "while Windows reads the AI libraries from disk.")

    def bring_to_front(self):
        self.show()
        self.raise_()
        self.activateWindow()


def load_heavy_parts(app: QApplication, loading: LoadingWindow):
    """Import everything the main window needs, keeping the loading window painted meanwhile."""
    loading.show_step("Loading the AI libraries…")

    def preload():
        for mod in HEAVY:
            try:
                importlib.import_module(mod)
            except Exception:  # noqa: BLE001  (imported again below, where the error is shown)
                pass

    worker = threading.Thread(target=preload, name="preload", daemon=True)
    worker.start()
    while worker.is_alive():
        app.processEvents()
        loading.tick()
        worker.join(0.05)
    for label, mod in STEPS:
        loading.show_step(f"Loading {label}…")
        app.processEvents()
        importlib.import_module(mod)
    loading.show_step("Opening the window…")
    app.processEvents()
    return sys.modules["wb_window"].MainWindow


def main() -> int:
    wb_lifecycle.bind_children_to_process()  # helper processes end when the workbench ends
    try:  # its own taskbar button and icon, instead of pythonw's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Lundqwist.AIWorkbench")
    except (AttributeError, OSError):
        pass
    app = QApplication(sys.argv)
    app.setApplicationName("AI Workbench")
    if (APP_DIR / "icon.ico").exists():
        app.setWindowIcon(QIcon(str(APP_DIR / "icon.ico")))
    app.setStyleSheet(THEME)
    screenshot = "--screenshot" in sys.argv
    if screenshot:
        sys.argv.append("--no-update-check")

    # Only one copy: a second start shows the first one (test runs with --screenshot are left alone)
    server = None
    window = None
    loading = LoadingWindow()
    if not screenshot:
        name = instance_name()
        if tell_running_copy(name):
            return 0
        server = QLocalServer()
        server.setSocketOptions(QLocalServer.UserAccessOption)
        if not server.listen(name):
            QLocalServer.removeServer(name)  # left over from a copy that crashed
            server.listen(name)

        def on_connection():
            while server.hasPendingConnections():
                conn = server.nextPendingConnection()
                conn.readyRead.connect(conn.readAll)
                conn.disconnected.connect(conn.deleteLater)
                (window or loading).bring_to_front()
        server.newConnection.connect(on_connection)

    loading.show()
    app.processEvents()
    if not screenshot:  # Ollama doesn't start at sign-in: start it now, so it's up when the window opens
        threading.Thread(target=wb_lifecycle.ensure_ollama, args=(settings.ollama_host,),
                         name="start-ollama", daemon=True).start()
    try:
        settings.ensure_folders()
        settings.apply()
        MainWindow = load_heavy_parts(app, loading)
        window = MainWindow(settings)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        loading.hide()
        QMessageBox.critical(None, "AI Workbench could not start",
                             f"{type(e).__name__}: {e}\n\nThe details are in\n{LOG}")
        return 1
    window.show()
    loading.close()
    window.raise_()
    window.activateWindow()
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Started in {time.monotonic() - loading.started:.1f} s")
    if screenshot:  # used by tests: save a picture of each tab and quit
        out = Path(sys.argv[sys.argv.index("--screenshot") + 1])
        out.mkdir(parents=True, exist_ok=True)

        def shoot(i=0):
            if i >= window.tabs.count():
                app.quit()
                return
            window.tabs.setCurrentIndex(i)
            QTimer.singleShot(1500, lambda: (window.grab().save(str(out / f"{i}-{window.tabs.tabText(i)}.png")),
                                             shoot(i + 1)))

        QTimer.singleShot(2500, shoot)
        return app.exec()
    code = app.exec()
    wb_lifecycle.stop_ollama(settings.ollama_host)  # the window is closed: end Ollama, also one started by hand
    return code


if __name__ == "__main__":
    code = main()
    # The window has closed and saved. Don't wait for background threads (a stuck Ollama request,
    # ssh, a microphone that won't stop): they used to keep pythonw.exe running with no window.
    wb_lifecycle.hard_exit(code)
