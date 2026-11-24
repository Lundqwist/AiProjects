"""On-screen permission questions for the assistant's tools.

Tools run on worker threads; a question has to be shown by the GUI thread. ask() hands it over
through a Qt signal and waits for the click. No answer within the time limit counts as a no, and
there is deliberately no way to answer by voice.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import QApplication, QMessageBox


class PermissionBroker(QObject):
    _request = Signal(object)
    _expire = Signal(object)

    def __init__(self, window=None, timeout: int = 90, on_ask=None):
        super().__init__()
        self.window = window
        self.timeout = timeout
        self.on_ask = on_ask or (lambda: None)  # e.g. a chime so the user looks at the screen
        self._request.connect(self._show, Qt.QueuedConnection)
        # Only the GUI thread may touch the question box; a worker that gives up asks it to close it
        self._expire.connect(lambda job: job.get("box") and job["box"].reject(), Qt.QueuedConnection)

    def ask(self, title: str, text: str, buttons: list[str]) -> str:
        """Called from any thread. Returns the clicked button; the last button (the "no") on timeout."""
        job = {"title": title, "text": text, "buttons": buttons, "answer": buttons[-1],
                "done": threading.Event()}
        if QThread.currentThread() is self.thread():
            self._show(job, modal=True)  # on the GUI thread itself: wait for the click here
        else:
            self._request.emit(job)
            if not job["done"].wait(self.timeout):
                job["expired"] = True
                self._expire.emit(job)  # takes the unanswered question off the screen
                return buttons[-1]
        return job["answer"]

    def _show(self, job: dict, modal: bool = False) -> None:
        self.on_ask()
        box = QMessageBox(self.window)
        box.setWindowTitle(f"AI Workbench: {job['title']}")
        box.setTextFormat(Qt.RichText)
        box.setText(job["text"] + "<br><br><span style='color:#9fb0cc'>Asked by the assistant. "
                    f"No answer in {self.timeout} s counts as no.</span>")
        box.setIcon(QMessageBox.Question)
        box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        buttons = {box.addButton(b, QMessageBox.RejectRole if i == len(job["buttons"]) - 1
                   else QMessageBox.AcceptRole): b for i, b in enumerate(job["buttons"])}
        box.setDefaultButton(list(buttons)[-1])  # Enter means no
        job["box"] = box
        if self.window:
            QApplication.alert(self.window)  # flashes the taskbar button
            self.window.showNormal()
            self.window.raise_()
            self.window.activateWindow()

        def finished(_):
            clicked = box.clickedButton()
            # a click after the time ran out changes nothing: the tool has already been told no
            job["answer"] = job["buttons"][-1] if job.get("expired") else buttons.get(clicked, job["buttons"][-1])
            job["done"].set()

        box.finished.connect(finished)
        if modal:
            box.exec()
        else:
            box.open()  # not exec(): the program keeps running while the question is up
