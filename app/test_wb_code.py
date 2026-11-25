"""Tests for the Code tab's reply parser (wb_code.parse). No models needed.

Run: .venv\\Scripts\\python.exe app\\test_wb_code.py

The replies below copy the shapes qwen2.5-coder really wrote in the golden-job runs (2026-11-25),
where the old parser lost correct code or glued the explanation into a file.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wb_code  # noqa: E402

F = "```"
failures: list[str] = []


def project() -> Path:
    root = Path(tempfile.mkdtemp(prefix="wb-code-test-"))
    (root / "Api").mkdir()
    (root / "Api" / "Program.cs").write_text("var app = 1;\n", encoding="utf-8")
    long = "".join(f"line {i}\n" for i in range(40))
    (root / "src").mkdir()
    (root / "src" / "cart.js").write_text(long, encoding="utf-8")
    return root


def case(name: str, reply: str, expect: dict[str, str | None], *, notes_has: str = "", unfinished=()):
    folder = project()
    notes: list[str] = []
    _, changes = wb_code.parse(reply, folder, notes)
    got = {c.path: c.new_text for c in changes}
    for path, text in expect.items():
        if text is None:
            if path in got:
                failures.append(f"{name}: {path} should have been left out")
        elif got.get(path) != text:
            failures.append(f"{name}: {path} = {got.get(path)!r}, expected {text!r}")
    extra = set(got) - set(expect)
    if extra:
        failures.append(f"{name}: unexpected files {sorted(extra)}")
    if notes_has and not any(notes_has in n for n in notes):
        failures.append(f"{name}: no note containing {notes_has!r} in {notes}")
    cut: list[str] = []
    wb_code._blocks(reply, cut)
    if list(unfinished) != cut:
        failures.append(f"{name}: unfinished {cut}, expected {list(unfinished)}")
    print(("FAIL  " if any(f.startswith(name + ":") for f in failures) else "PASS  ") + name)


case("strict format, two files",
     f"Done.\n\n=== FILE: Api/Program.cs ===\n{F}csharp\nvar app = 2;\n{F}\n\n=== FILE: README.md ===\n{F}markdown\n# Hi\n{F}\n",
     {"Api/Program.cs": "var app = 2;\n", "README.md": "# Hi\n"})

case("heading with prose before the code, bare file name",
     f"### Changes to `Program.cs`\n\nWe add the endpoints.\n\n{F}csharp\nvar app = 3;\n{F}\n",
     {"Api/Program.cs": "var app = 3;\n"})

case("explanation code blocks after the last file are not glued into it",
     f"=== FILE: Api/Program.cs ===\n{F}csharp\nvar app = 4;\n{F}\n\n### How to Run\n\n1. Run:\n   {F}sh\n   dotnet run\n   {F}\n",
     {"Api/Program.cs": "var app = 4;\n"})

case("README with nested example blocks stays whole",
     f"=== FILE: README.md ===\n{F}markdown\n# Tool\n\n{F}powershell\n.\\run.ps1\n{F}\n\nDone.\n{F}\n",
     {"README.md": f"# Tool\n\n{F}powershell\n.\\run.ps1\n{F}\n\nDone.\n"})

case("a cut-off file is left out and reported",
     f"=== FILE: a.py ===\n{F}python\nprint(1)\n{F}\n=== FILE: b.py ===\n{F}python\nprint(2",
     {"a.py": "print(1)\n", "b.py": None}, unfinished=["b.py"])

case("a fragment under a loose heading does not replace the file",
     f"### Updated `src/cart.js`:\n\n{F}js\nexport function applyCode() {{}}\n{F}\n",
     {"src/cart.js": None}, notes_has="looks like a part of the file")

case("a comment naming a file inside code is not a heading",
     f"=== FILE: tool.py ===\n{F}python\nimport json\n# reads config.json\n\nprint(json)\n{F}\n",
     {"tool.py": "import json\n# reads config.json\n\nprint(json)\n"})

case("an example block before the files is skipped",
     f"Run it like this:\n\n{F}sh\n# setup.py\npython setup.py\n{F}\n\n=== FILE: setup.py ===\n{F}python\nprint('ok')\n{F}\n",
     {"setup.py": "print('ok')\n"})

case("bold file name heading",
     f"**src/util.js**\n{F}js\nexport const x = 1;\n{F}\n",
     {"src/util.js": "export const x = 1;\n"})

if failures:
    print("\n" + "\n".join(failures))
    sys.exit(1)
print("\nAll parser tests passed.")
