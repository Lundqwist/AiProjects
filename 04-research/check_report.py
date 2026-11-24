"""Grounding check: list numbers, IDs and code names in a report that never appeared
in anything the agent actually read. A cheap way to catch made-up facts.

Usage: python check_report.py [reports/<stamp>.md]   (default: the newest report)
"""
import json
import re
import sys
from pathlib import Path

REPORTS = Path(__file__).parent / "reports"
PATTERN = re.compile(r"CVE-[\d-]+|\b\d+(?:\.\d+)+\b|\b\d{3,}\b|`[^`]+`")


def check(report: Path) -> tuple[int, list[str]]:
    log = report.with_name(report.stem + ".log.jsonl")
    seen = " ".join(json.loads(line)["result"] for line in log.read_text(encoding="utf-8").splitlines()).lower()
    body = report.read_text(encoding="utf-8").split("Pages actually read")[0]
    body = re.sub(r"https?://\S+", "", body)  # URLs are checked by the reading list, not here
    terms = sorted(set(PATTERN.findall(body)))
    return len(terms), [t for t in terms if t.strip("`").lower() not in seen]


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else max(REPORTS.glob("*.md"), key=lambda p: p.stat().st_mtime)
    total, missing = check(path)
    print(f"{path.name}: {total} numbers/IDs/code names checked, {len(missing)} not found in the sources")
    for t in missing:
        print("  NOT IN SOURCES:", t)
    sys.exit(1 if missing else 0)
