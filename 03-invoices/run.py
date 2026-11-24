"""Process every invoice in inbox/ once, or keep watching it.

Usage: python run.py          (process what's there now)
       python run.py --watch  (and keep processing new files)
"""
import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

import invoice

HERE = Path(__file__).parent
INBOX = HERE / "inbox"
OUT = HERE / "output"
SUFFIXES = {".pdf"} | invoice.IMAGE_SUFFIXES


def handle(path: Path) -> None:
    if path.suffix.lower() not in SUFFIXES or not path.exists():
        return
    start = time.time()
    ok, msg = invoice.process(path, OUT)
    print(f"{'OK    ' if ok else 'REVIEW'} {path.name} ({time.time() - start:.0f}s): {msg}")


class Handler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory:
            time.sleep(1)  # let the copy finish
            handle(Path(event.src_path))


def main() -> int:
    INBOX.mkdir(exist_ok=True)
    for path in sorted(INBOX.iterdir()):
        handle(path)
    if "--watch" in sys.argv:
        obs = Observer()
        obs.schedule(Handler(), str(INBOX))
        obs.start()
        print(f"Watching {INBOX} (Ctrl+C to stop)")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            obs.stop()
        obs.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
