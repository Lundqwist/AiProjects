# Creates (or with -Remove deletes) the "AI Workbench" shortcut in the Start menu.
# Run from anywhere: powershell -ExecutionPolicy Bypass -File app\install-shortcut.ps1 [-Desktop] [-Remove]
param([switch]$Desktop, [switch]$Remove)

$root = Split-Path -Parent $PSScriptRoot
$targets = @([Environment]::GetFolderPath('Programs'))
if ($Desktop) { $targets += [Environment]::GetFolderPath('Desktop') }

foreach ($dir in $targets) {
    $lnk = Join-Path $dir 'AI Workbench.lnk'
    if ($Remove) {
        if (Test-Path $lnk) { Remove-Item $lnk; "Removed $lnk" }
        continue
    }
    $pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path $pythonw)) { throw "No virtual environment at $root\.venv - see the README's Setup section." }
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $s.TargetPath = $pythonw
    $s.Arguments = '"' + (Join-Path $PSScriptRoot 'main.py') + '"'
    $s.WorkingDirectory = $root
    $s.IconLocation = (Join-Path $PSScriptRoot 'icon.ico')
    $s.Description = 'Local AI: documents, voice, invoices, research, evaluation'
    $s.Save()
    "Created $lnk"
}
