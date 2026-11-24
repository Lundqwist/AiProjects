# 02 · AI voice agent

Talk to it, it talks back, in English or Swedish. Speech-to-text, the answer and the voice all run locally.

**Stack:** [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (`small`) · Ollama `qwen3:8b` with tool calling · [Piper](https://github.com/OHF-Voice/piper1-gpl) voices `en_US-lessac-medium` and `sv_SE-nst-medium`

## How it works

1. Record from the microphone (Enter to start, Enter to stop) and transcribe with Whisper, which also detects the language.
2. Send the text to `qwen3:8b` with a system prompt written for speech: 1–3 short sentences, no lists or Markdown.
3. The model may call a tool. `open_tasks` reads open tasks from a [Vikunja](https://vikunja.io) to-do list and returns a count plus the 3 due soonest, so the spoken answer stays short.
4. Speak the answer with the Piper voice that matches the detected language.

## Run

```powershell
.\.venv\Scripts\Activate.ps1
cd 02-voice
python -m piper.download_voices en_US-lessac-medium sv_SE-nst-medium --data-dir voices
python voice.py
```

Optional: set `VIKUNJA_URL` and `VIKUNJA_TOKEN` to let it read your tasks.

Test without a microphone: `python make_test_audio.py` makes spoken questions with Piper, then
`python voice.py --file test-audio\q-sv.wav --out reply.wav`.

## Results (RTX 3080 Ti, i9-12900K)

| Step | Time |
|---|---|
| Speech-to-text (Whisper small, CPU int8) | ~3 s |
| Answer (qwen3:8b, warm) | 0.1–3.5 s; 2–7 s with a tool call |
| Voice (Piper) | < 1 s |

## What I learned

- **faster-whisper loads on the GPU without cuBLAS/cuDNN, then fails on first use.** The agent catches that and switches to CPU. To use the GPU: `uv pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` (about 1 GB).
- **faster-whisper's file decoding (PyAV) broke after a version mismatch**, so WAV files are read with `soundfile` and resampled to 16 kHz in numpy instead.
- **A tool's output shapes the spoken answer.** Returning 10 tasks got a 10-item list read aloud. Returning a count, the 3 soonest and an instruction gives one sentence.
