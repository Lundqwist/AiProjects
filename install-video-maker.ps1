# Installs the video model for AI Workbench's Create -> Movie "Moving scenes": LTX-Video 2B 0.9.8 distilled
# by Lightricks (LTX-Video Open Weights License), ~25 GB into .\.hf:
#   - ltxv-2b-0.9.8-distilled.safetensors from huggingface.co/Lightricks/LTX-Video (transformer + VAE, 6.3 GB)
#   - text encoder (T5), tokenizer and scheduler from huggingface.co/Lightricks/LTX-Video-0.9.5 (~19 GB)
# Needs the Create packages first (requirements-create.txt). Run from the AiProjects folder:
#   powershell -ExecutionPolicy Bypass -File .\install-video-maker.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$env:HF_HOME = Join-Path $root ".hf"
$env:HF_HUB_DISABLE_TELEMETRY = "1"
$py = Join-Path $root ".venv\Scripts\python.exe"
& $py -c "from huggingface_hub import snapshot_download, hf_hub_download; print(snapshot_download('Lightricks/LTX-Video-0.9.5', allow_patterns=['text_encoder/*','tokenizer/*','scheduler/*','model_index.json'])); print(hf_hub_download('Lightricks/LTX-Video', 'ltxv-2b-0.9.8-distilled.safetensors'))"
if ($LASTEXITCODE -ne 0) { throw "download failed" }
& $py -c "import sys; sys.path.insert(0, r'$root\app'); import wb_video; print(wb_video.installed())"
Write-Host "Video maker ready: Create -> Movie now has 'Moving scenes'."
