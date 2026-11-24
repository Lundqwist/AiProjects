"""Applies updates that can't happen while AI Workbench runs, then starts it again.

Started by the program with a plan file; waits for the program to exit first, because Windows
locks the files of loaded Python packages. Uses only the standard library, so it still works
while packages are being replaced.
"""
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def wait_for_exit(pid: int, timeout: int = 120) -> None:
    import ctypes
    SYNCHRONIZE = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if handle:
        ctypes.windll.kernel32.WaitForSingleObject(handle, timeout * 1000)
        ctypes.windll.kernel32.CloseHandle(handle)
    time.sleep(1)


def main() -> int:
    plan_file = Path(sys.argv[1])
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    log_file = plan_file.with_name("update.log")
    ok = True

    def log(msg: str) -> None:
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
        print(line, flush=True)
        with log_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def run(args: list[str]) -> bool:
        log("> " + " ".join(args))
        r = subprocess.run(args, cwd=plan["root"], text=True, capture_output=True, encoding="utf-8",
                           errors="replace")
        for line in (r.stdout + r.stderr).strip().splitlines()[-15:]:
            log("  " + line)
        return r.returncode == 0

    print("AI Workbench updates\n====================")
    log("Waiting for AI Workbench to close...")
    wait_for_exit(plan["pid"])

    if plan.get("git_pull"):
        log("Updating the program code (git pull --ff-only)...")
        ok &= run(["git", "pull", "--ff-only"])
    packages = plan.get("packages") or []
    if packages or plan.get("git_pull"):
        if not plan.get("uv"):
            log("uv was not found, so Python packages were not updated. Install it: pip install --user uv")
            ok = False
        else:
            req = Path(plan["root"]) / "requirements.txt"
            # Explicit upgrades, plus whatever requirements.txt (maybe just pulled) now asks for
            args = [*packages] + (["-r", str(req)] if req.exists() else [])
            if args:
                log("Updating Python packages...")
                ok &= run([plan["uv"], "pip", "install", "--python", plan["python"], *args])

    plan_file.unlink(missing_ok=True)
    log("Done." if ok else f"Finished with problems. Details: {log_file}")
    log("Starting AI Workbench...")
    subprocess.Popen(plan["relaunch"], cwd=plan["root"])
    if not ok:
        try:
            input("\nPress Enter to close this window.")
        except EOFError:
            pass
    else:
        time.sleep(3)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
