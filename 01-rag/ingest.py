"""Add PDF, Markdown and text files to the RAG database.

Usage: python ingest.py <file or folder> [...]
"""
import sys
from pathlib import Path

import rag

SUFFIXES = {".pdf"} | rag.TEXT_SUFFIXES


def main(args: list[str]) -> int:
    if not args:
        print(__doc__)
        return 1
    total_files = total_chunks = 0
    for arg in args:
        target = Path(arg).resolve()
        root = target if target.is_dir() else target.parent
        files = sorted(p for p in target.rglob("*") if p.suffix.lower() in SUFFIXES) if target.is_dir() else [target]
        for path in files:
            try:
                n = rag.ingest_file(path, root)
            except Exception as e:  # one broken PDF shouldn't stop the run
                print(f"  skipped {path.name}: {e}")
                continue
            print(f"  {path.name}: {n} chunks")
            total_files += 1
            total_chunks += n
    print(f"Done: {total_files} files, {total_chunks} chunks. Database now holds {rag.collection().count()} chunks.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
