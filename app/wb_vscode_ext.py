"""Build the AI Workbench extension for VS Code and install it, from Settings → VS Code.

The extension's source is vscode-extension/ in this repository (plain JavaScript, no npm packages). A .vsix
is only a zip with a manifest, so it is built here with zipfile instead of needing Node and vsce, then
installed with `code --install-extension`.
"""
from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import wb_code
import wb_lifecycle
import wb_settings

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "vscode-extension"
SKIP = {".vsix", ".log"}

CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension=".json" ContentType="application/json"/>
<Default Extension=".js" ContentType="application/javascript"/>
<Default Extension=".css" ContentType="text/css"/>
<Default Extension=".md" ContentType="text/markdown"/>
<Default Extension=".png" ContentType="image/png"/>
<Default Extension=".svg" ContentType="image/svg+xml"/>
<Default Extension=".vsixmanifest" ContentType="text/xml"/>
</Types>
"""

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011"
  xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">
  <Metadata>
    <Identity Language="en-US" Id="{name}" Version="{version}" Publisher="{publisher}"/>
    <DisplayName>{display}</DisplayName>
    <Description xml:space="preserve">{description}</Description>
    <Tags>{tags}</Tags>
    <Categories>{categories}</Categories>
    <GalleryFlags>Public</GalleryFlags>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{engine}"/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionDependencies" Value=""/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionPack" Value=""/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="workspace"/>
      <Property Id="Microsoft.VisualStudio.Code.LocalizedLanguages" Value=""/>
    </Properties>
    <Icon>extension/{icon}</Icon>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code"/>
  </Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/>
    <Asset Type="Microsoft.VisualStudio.Services.Content.Details" Path="extension/README.md" Addressable="true"/>
    <Asset Type="Microsoft.VisualStudio.Services.Icons.Default" Path="extension/{icon}" Addressable="true"/>
  </Assets>
</PackageManifest>
"""


def package_info() -> dict:
    return json.loads((SOURCE / "package.json").read_text(encoding="utf-8"))


def build(out_dir: Path | None = None) -> Path:
    """Make ai-workbench-<version>.vsix from vscode-extension/ and return its path."""
    info = package_info()
    out_dir = out_dir or wb_settings.SETTINGS_FILE.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{info['name']}-{info['version']}.vsix"
    manifest = MANIFEST.format(
        name=escape(info["name"]), version=escape(info["version"]), publisher=escape(info["publisher"]),
        display=escape(info.get("displayName", info["name"])), description=escape(info.get("description", "")),
        tags=escape(",".join(info.get("keywords", []))), categories=escape(",".join(info.get("categories", []))),
        engine=escape(info["engines"]["vscode"]), icon=escape(info.get("icon", "media/icon.png")))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("extension.vsixmanifest", manifest)
        for f in sorted(SOURCE.rglob("*")):
            if f.is_file() and f.suffix not in SKIP and not any(p.startswith(".") for p in f.relative_to(SOURCE).parts):
                z.write(f, "extension/" + f.relative_to(SOURCE).as_posix())
    return out


def installed_version() -> str | None:
    """The version VS Code has installed, or None (asks `code --list-extensions --show-versions`)."""
    if not wb_code.vscode():
        return None
    info = package_info()
    want = f"{info['publisher']}.{info['name']}".lower()
    try:
        cli, env = wb_code.code_cli()
        out = subprocess.run(cli + ["--list-extensions", "--show-versions"], capture_output=True, text=True,
                             timeout=60, env=env, creationflags=wb_lifecycle.NO_WINDOW).stdout
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        ext, _, version = line.strip().partition("@")
        if ext.lower() == want:
            return version
    return None


def install(emit=lambda m: None) -> str:
    """Build and install the extension. Returns what happened, for the screen."""
    cli, env = wb_code.code_cli()  # says so if VS Code isn't installed
    emit("Building the extension…")
    vsix = build()
    emit("Installing it in VS Code…")
    r = subprocess.run(cli + ["--install-extension", str(vsix), "--force"], capture_output=True, text=True,
                       timeout=180, env=env, creationflags=wb_lifecycle.NO_WINDOW)
    text = (r.stdout + r.stderr).strip()
    if r.returncode != 0 or "error" in text.lower() and "successfully" not in text.lower():
        raise RuntimeError(text or f"code --install-extension ended with {r.returncode}")
    version = package_info()["version"]
    return (f"Installed AI Workbench {version} in VS Code. Reload VS Code windows that are open "
            "(Ctrl+Shift+P → Developer: Reload Window), then open the AI Workbench view in the activity bar.")
