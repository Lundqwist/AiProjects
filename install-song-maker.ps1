# Installs the song maker for AI Workbench's Create -> Song: ACE-Step 1.5 (github.com/ACE-Step/ACE-Step-1.5, MIT)
# in .\ace-step with its own uv environment (its own CUDA PyTorch) and models (~13 GB in all).
# Run from the AiProjects folder:  powershell -ExecutionPolicy Bypass -File .\install-song-maker.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$ace = Join-Path $root "ace-step"
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { $uv = Join-Path $env:APPDATA "Python\Python314\Scripts\uv.exe" }
if (-not (Test-Path $uv)) { throw "uv not found: install it with 'pip install --user uv'" }
# Keep uv's Python and cache inside the project (the Claude app redirects AppData\Local)
$env:UV_PYTHON_INSTALL_DIR = Join-Path $root ".python"
$env:UV_CACHE_DIR = Join-Path $root ".uv-cache"
$env:HF_HUB_DISABLE_TELEMETRY = "1"

if (-not (Test-Path $ace)) {
    git clone --depth 1 https://github.com/ACE-Step/ACE-Step-1.5.git $ace
} else {
    Write-Host "ace-step already there: updating it"
    git -C $ace pull --ff-only
}
Push-Location $ace
try {
    & $uv sync --python 3.12
    & .venv\Scripts\python.exe -c "import torch; assert torch.cuda.is_available(), 'no CUDA'; print('PyTorch', torch.__version__, 'sees', torch.cuda.get_device_name(0))"
    & .venv\Scripts\acestep-download.exe                                  # main model (DiT turbo, VAE, text encoder)
    & .venv\Scripts\acestep-download.exe --model acestep-5Hz-lm-0.6B --skip-main   # planner that fits 12 GB cards
} finally { Pop-Location }
Write-Host "Song maker ready. AI Workbench starts it on 127.0.0.1:8001 the first time you make a song."
