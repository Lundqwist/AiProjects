"""Songs with singing: lyrics by the writing expert (or your own), sung and played by ACE-Step 1.5.

ACE-Step 1.5 (github.com/ACE-Step/ACE-Step-1.5, MIT) lives in its own folder AiProjects\\ace-step with
its own environment and models (~10 GB), set up by install-song-maker.ps1. It needs other package
versions than the workbench (its own CUDA PyTorch), so it runs as a separate local server on
127.0.0.1:8001 that the workbench starts the first time a song is made. It is a child process, so it
ends when the workbench closes. Its models move to the CPU when idle, so Ollama keeps the GPU.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.parse
from pathlib import Path

import httpx

import wb_ai
import wb_create
from wb_settings import ROOT, SETTINGS_DIR, Settings

ACE_DIR = ROOT / "ace-step"
PORT = 8001
URL = f"http://127.0.0.1:{PORT}"
LOG = SETTINGS_DIR / "ace-step.log"
LM_MODEL = "acestep-5Hz-lm-0.6B"   # the "thinking" model that plans the song; 1.7B is refused on 12 GB cards
DIT_MODEL = "acestep-v15-turbo"
LANG_CODES = {"English": "en", "Swedish": "sv"}
LENGTHS = {"Short (≈1 min)": 60, "Medium (≈2 min)": 120, "Long (≈3 min)": 180}
MAX_SECONDS = 480  # ACE-Step's limit with the planning LM on a 12 GB card
_server: subprocess.Popen | None = None


def bpm_of(style: str, default: int = 110) -> int:
    """The tempo named in the style tags ("…, 120 BPM"), kept to a singable 60-180."""
    m = re.search(r"(\d{2,3})\s*bpm", style, re.I)
    return max(60, min(180, int(m.group(1)))) if m else default


def seconds_needed(lyrics: str, bpm: int) -> int:
    """How long the lyrics take to sing: about two bars (8 beats) per sung line, a bar of breath
    between parts, and room for an intro and an outro. A song cut at a fixed length used to end
    mid-verse when the lyrics were longer than the length chosen."""
    beat = 60 / bpm
    lines = [l.strip() for l in lyrics.splitlines() if l.strip()]
    sung = [l for l in lines if not l.startswith(("[", "("))]
    parts = sum(1 for l in lines if l.startswith("["))
    return round(len(sung) * 8 * beat + parts * 4 * beat + 20)


def installed() -> tuple[bool, str]:
    py = ACE_DIR / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        return False, "ACE-Step is not installed: run install-song-maker.ps1 in AiProjects"
    if not (ACE_DIR / "checkpoints" / DIT_MODEL).exists():
        return False, "ACE-Step's models are missing: run install-song-maker.ps1 in AiProjects"
    return True, "ACE-Step 1.5"


def _healthy() -> bool:
    try:
        return httpx.get(f"{URL}/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def _log_tail() -> str:
    try:
        lines = [l for l in LOG.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        return re.sub(r"\x1b\[[0-9;]*m", "", lines[-1])[-160:] if lines else ""
    except OSError:
        return ""


def ensure_server(emit=lambda m: None, timeout: float = 900) -> None:
    """Start the ACE-Step server unless it already answers, and wait until it does."""
    global _server
    if _healthy():
        return
    ok, why = installed()
    if not ok:
        raise RuntimeError(why)
    if _server is None or _server.poll() is not None:
        env = dict(os.environ, ACESTEP_API_HOST="127.0.0.1", ACESTEP_API_PORT=str(PORT),
                   ACESTEP_CONFIG_PATH=DIT_MODEL, ACESTEP_LM_MODEL_PATH=LM_MODEL, ACESTEP_LM_BACKEND="pt",
                   ACESTEP_OFFLOAD_TO_CPU="true", HF_HUB_DISABLE_TELEMETRY="1", PYTHONIOENCODING="utf-8")
        for k in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"):  # the workbench's own venv must not leak in
            env.pop(k, None)
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        log = open(LOG, "w", encoding="utf-8")  # noqa: SIM115 - the server keeps writing to it
        _server = subprocess.Popen([str(ACE_DIR / ".venv" / "Scripts" / "python.exe"), "-m", "acestep.api_server"],
                                   cwd=ACE_DIR, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=0x08000000)  # no window; ends with the workbench (job object)
    start = time.time()
    while time.time() - start < timeout:
        if _server.poll() is not None:
            raise RuntimeError(f"ACE-Step stopped while starting: {_log_tail()} (log: {LOG})")
        if _healthy():
            return
        emit(f"Starting the song maker ({time.time() - start:.0f} s)… {_log_tail()[:90]}")
        time.sleep(2)
    raise RuntimeError(f"ACE-Step didn't start within {timeout / 60:.0f} minutes (log: {LOG})")


def stop_server() -> None:
    """End the server and its child (the venv's python.exe is a launcher for the real interpreter),
    so the graphics memory is free again for Ollama."""
    global _server
    if _server and _server.poll() is None:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(_server.pid)], capture_output=True,
                       creationflags=0x08000000)
        try:
            _server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _server.kill()
    _server = None


def _data(r: httpx.Response):
    r.raise_for_status()
    body = r.json()
    if body.get("code", 200) != 200 or body.get("error"):
        raise RuntimeError(f"ACE-Step: {body.get('error') or body}")
    return body.get("data")


def sing(style: str, lyrics: str, seconds: int, language: str, emit=lambda m: None,
         timeout: float = 900, bpm: int | None = None) -> tuple[bytes, dict]:
    """Have ACE-Step sing the lyrics in the style. Returns (wav bytes, what ACE-Step reports).
    WAV, because ACE-Step's own MP3 export needs ffmpeg on PATH; make_song converts it.
    The tempo is given, so the planning model can't race through long lyrics (it once chose 300 BPM)."""
    ensure_server(emit)
    payload = {"prompt": style, "lyrics": lyrics, "audio_duration": float(seconds), "bpm": bpm or bpm_of(style),
               "vocal_language": LANG_CODES.get(language, "en"), "audio_format": "wav", "batch_size": 1,
               "thinking": True, "inference_steps": 8, "model": DIT_MODEL}
    task = _data(httpx.post(f"{URL}/release_task", json=payload, timeout=60))["task_id"]
    start = time.time()
    while time.time() - start < timeout:
        items = _data(httpx.post(f"{URL}/query_result", json={"task_id_list": [task]}, timeout=60)) or []
        item = items[0] if items else {}
        status = item.get("status", 0)
        if status == 2:
            raise RuntimeError(f"ACE-Step couldn't make the song: {item.get('result') or _log_tail()}")
        if status == 1:
            result = item.get("result")
            result = json.loads(result) if isinstance(result, str) else result
            first = result[0] if isinstance(result, list) else result
            file_url = first.get("file") or ""
            if not file_url:
                raise RuntimeError(f"ACE-Step made the song but couldn't save it: {_log_tail()}")
            if not file_url.startswith("http"):
                file_url = URL + (file_url if file_url.startswith("/") else
                                  f"/v1/audio?path={urllib.parse.quote(file_url, safe='')}")
            audio = httpx.get(file_url, timeout=120)
            audio.raise_for_status()
            return audio.content, first
        emit(f"Singing… ({time.time() - start:.0f} s)")
        time.sleep(2)
    raise RuntimeError(f"ACE-Step took longer than {timeout / 60:.0f} minutes")


def _to_mp3(wav: bytes, out: Path, seconds: float) -> None:
    """WAV bytes to a 192 kbit/s MP3 with the ffmpeg from imageio-ffmpeg, with a short fade at the
    end so the last note never stops abruptly."""
    import wb_movie
    fade = f"afade=t=out:st={max(0.0, seconds - 4):.2f}:d=4"
    r = subprocess.run([wb_movie.ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "wav", "-i", "-",
                        "-af", fade, "-codec:a", "libmp3lame", "-b:a", "192k", str(out)],
                       input=wav, capture_output=True, creationflags=0x08000000)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg couldn't make the MP3: {r.stderr.decode(errors='replace')[-200:]}")


def _json_from(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("not JSON")
    return json.loads(text[a:b + 1])


def write_lyrics(s: Settings, idea: str, style: str, language: str, seconds: int,
                 online: bool | None) -> tuple[dict, wb_ai.Answer]:
    shape, lines = ((("[Intro], [Verse 1], [Chorus], [Outro]"), 10) if seconds <= 70 else
                    (("[Intro], [Verse 1], [Chorus], [Verse 2], [Chorus], [Outro]"), 20) if seconds <= 130 else
                    (("[Intro], [Verse 1], [Chorus], [Verse 2], [Chorus], [Bridge], [Chorus], [Outro]"), 30))
    system = (
        "You are a songwriter. Reply with JSON only, no other text:\n"
        '{"title": "<song title>", "style": "<English, comma-separated tags: genre, mood, main instruments, '
        'vocal (e.g. female vocal / male vocal), tempo in BPM>", "lyrics": "<the lyrics>"}\n'
        f"Write singable, rhyming lyrics in {language} with exactly these parts, each marker on its own line: "
        f"{shape}. At most {lines} sung lines in all, 4 lines per verse and chorus; [Intro] and [Outro] have no "
        "words. Short lines, 6-10 syllables each. Use \\n for new lines inside the JSON string."
        + (f" The style should be: {style}." if style else ""))
    last = ""
    for _ in range(2):
        a = wb_ai.ask(s, [{"role": "system", "content": system},
                          {"role": "user", "content": idea + (f"\n\n(Last reply failed: {last}. JSON only.)" if last else "")}],
                      task="story", expert="writing", big=online, temperature=0.85, max_tokens=2000)
        try:
            song = _json_from(a.text)
            if len(str(song.get("lyrics", "")).strip()) < 40:
                raise ValueError("no lyrics")
            song.setdefault("title", idea[:50])
            song["style"] = style or song.get("style") or "pop, upbeat, female vocal, 110 BPM"
            return song, a
        except (ValueError, TypeError) as e:
            last = str(e)
    raise RuntimeError(f"The writing expert didn't write usable lyrics ({last}).")


def make_song(s: Settings, idea: str, style: str = "", language: str = "English", length: str = "Short (≈1 min)",
              own_lyrics: str = "", online: bool | None = None, emit=lambda m: None) -> wb_create.Made:
    ok, why = installed()
    if not ok:
        raise RuntimeError(why)
    seconds = LENGTHS.get(length, 60)
    notes: list[str] = []
    if own_lyrics.strip():
        song = {"title": idea.strip().splitlines()[0][:60] if idea.strip() else "My song",
                "style": style or "pop, upbeat, female vocal, 110 BPM", "lyrics": own_lyrics.strip()}
        writer = "you"
        notes.append("lyrics written by you")
    else:
        emit("Writing the lyrics…")
        song, a = write_lyrics(s, idea, style, language, seconds, online)
        writer = a.by
        notes.append(f"lyrics and style written by {a.by} {a.where}")
        notes += a.notes
    bpm = bpm_of(song["style"])
    needed = seconds_needed(song["lyrics"], bpm)
    if needed > seconds:
        notes.append(f"made {min(needed, MAX_SECONDS)} s long instead of {seconds} s, so all the lyrics are sung")
    if needed > MAX_SECONDS:
        notes.append(f"the lyrics need about {needed} s but ACE-Step stops at {MAX_SECONDS} s: the end may be cut")
    seconds = min(max(seconds, needed), MAX_SECONDS)
    unloaded = wb_create.free_gpu()  # ACE-Step needs the graphics memory Ollama holds
    if unloaded:
        notes.append(f"unloaded {', '.join(unloaded)} from the graphics card to make room")
    t = time.time()
    with wb_create._lock:
        wav, info = sing(song["style"], song["lyrics"], seconds, language, emit, bpm=bpm)
    out = wb_create.folder(s, "song") / f"{wb_create._stamp()}-{wb_create._slug(song['title'])}.mp3"
    _to_mp3(wav, out, seconds)
    out.with_suffix(".txt").write_text(f"{song['title']}\n\n{song['lyrics']}\n", encoding="utf-8")
    metas = info.get("metas") or {}
    made = wb_create.Made(out, "song", idea, f"ACE-Step 1.5 ({DIT_MODEL} + {LM_MODEL})",
                          f"on this PC (GPU, {time.time() - t:.0f} s)", notes,
                          {"title": song["title"], "style": song["style"], "lyrics": song["lyrics"],
                           "language": language, "seconds": seconds, "lyrics_by": writer,
                           "bpm": metas.get("bpm"), "key": metas.get("keyscale"), "seed": info.get("seed_value")})
    made.save_record()
    return made
