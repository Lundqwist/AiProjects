"""Messages from background jobs to the conversation: "The song is ready", "The report is done".

Jobs that take minutes (songs, movies, research reports) run apart from the turn that started them.
When one ends, it calls announce(); the conversation shows it and keeps it in the assistant's memory
of the conversation, so "play it again" or "what did the report say?" work afterwards.
Listeners are called on the job's own thread: the conversation hops to the GUI thread itself.
"""
from __future__ import annotations

_listeners: list = []


def subscribe(fn) -> None:
    if fn not in _listeners:
        _listeners.append(fn)


def unsubscribe(fn) -> None:
    if fn in _listeners:
        _listeners.remove(fn)


def announce(text: str, path: str = "") -> None:
    for fn in list(_listeners):
        try:
            fn(text, path)
        except Exception as e:  # noqa: BLE001 - a broken listener must not break the job
            print(f"notify: {type(e).__name__}: {e}")
