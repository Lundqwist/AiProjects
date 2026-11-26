"""Make sure AI Workbench ends everything it started when it closes.

Two parts:
  * A Windows job object: every helper process the program starts (ssh, PowerShell, promptfoo, the
    promptfoo viewer, ...) is put in the same job, and Windows ends the whole job when this process
    ends, even if it crashes or is ended in Task Manager.
  * hard_exit(): after the window has closed and saved, end the process at once instead of waiting
    for background threads (a stuck Ollama request, a microphone that won't stop), which used to
    leave pythonw.exe running with no window.
  * Ollama: ensure_ollama() starts it when the workbench opens (it no longer starts at sign-in) and
    stop_ollama() ends all of it when the workbench closes, also when it was started by hand.

Programs opened *for the user* (Chrome, Spotify, a file in its default program, the updater) must
outlive the workbench: start those with `start_independent`, which breaks them out of the job.
"""
from __future__ import annotations

import os
import subprocess
import sys

CREATE_BREAKAWAY_FROM_JOB = 0x01000000
NO_WINDOW = 0x08000000

_job = None  # kept for the process lifetime: closing the handle would end the job early


def bind_children_to_process() -> bool:
    """Put this process in a job that ends all its child processes when it ends."""
    global _job
    if os.name != "nt" or _job is not None:
        return _job is not None
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    k32.GetCurrentProcess.restype = wintypes.HANDLE

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount",
                    "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x0800
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9

    job = k32.CreateJobObjectW(None, None)
    if not job:
        return False
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK
    if not k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation,
                                       ctypes.byref(info), ctypes.sizeof(info)):
        k32.CloseHandle(job)
        return False
    if not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
        k32.CloseHandle(job)  # e.g. already in a job that forbids nesting: carry on without
        return False
    _job = job
    return True


def start_independent(args: list[str], **kwargs) -> subprocess.Popen:
    """Start a program that must keep running after the workbench closes."""
    flags = kwargs.pop("creationflags", 0)
    if os.name == "nt":
        try:
            return subprocess.Popen(args, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kwargs)
        except OSError:
            pass  # a parent job (not ours) forbids breaking away: start it normally instead
    return subprocess.Popen(args, creationflags=flags, **kwargs)


def open_independent(path: str) -> None:
    """Open a file or folder in its default program, outside the job (instead of os.startfile)."""
    if os.name == "nt":
        start_independent(["explorer.exe", path])
    else:
        os.startfile(path)  # noqa: S606


OLLAMA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama")
# The tray app first (it would start the server again), then the server and its model runners
OLLAMA_IMAGES = ("ollama app.exe", "ollama.exe", "ollama_llama_server.exe")


def _is_local(host: str) -> bool:
    from urllib.parse import urlparse
    name = urlparse(host if "://" in host else f"http://{host}").hostname or ""
    return name in ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def _ollama_answers(host: str, timeout: float = 1.5) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/version", timeout=timeout):
            return True
    except OSError:
        return False


def ensure_ollama(host: str) -> bool:
    """Start Ollama's server (hidden, inside the job) if it isn't running. True if one was started.

    Ollama no longer starts at sign-in, so the workbench starts it. Being in the job, it also ends
    if the workbench crashes or is ended in Task Manager.
    """
    if os.name != "nt" or not _is_local(host) or _ollama_answers(host):
        return False
    exe = os.path.join(OLLAMA_DIR, "ollama.exe")
    if not os.path.exists(exe):
        return False
    env = {**os.environ, "OLLAMA_HOST": host.split("://", 1)[-1]}
    subprocess.Popen([exe, "serve"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    return True


def stop_ollama(host: str) -> None:
    """End every Ollama process (tray app, server, model runners), also one the user started."""
    if os.name != "nt" or not _is_local(host):
        return
    for image in OLLAMA_IMAGES:
        subprocess.run(["taskkill", "/F", "/T", "/IM", image], capture_output=True, creationflags=NO_WINDOW)


def hard_exit(code: int = 0) -> None:
    """End the process now: background threads can't keep it alive, and the job ends its children."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001
            pass
    os._exit(code)
