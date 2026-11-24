"""Ask a question about the ingested documents.

Usage: python ask.py "your question"
"""
import sys

import rag


def main(args: list[str]) -> int:
    if not args:
        print(__doc__)
        return 1
    question = " ".join(args)
    hits = rag.search(question)
    for part in rag.answer_stream(question, hits):
        print(part, end="", flush=True)
    print("\n\nSources:")
    for i, h in enumerate(hits, 1):
        print(f"  [{i}] {h.label}  (distance {h.distance:.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
