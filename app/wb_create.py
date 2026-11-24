"""Make pictures, music, songs, stories and movies, and keep a record of who made each one.

Pictures: an online AI with an image model while it has tokens (wb_ai.online_image), else
SDXL-Turbo on this PC's GPU. Music: MusicGen (Meta) on this PC. Songs with singing: lyrics by the
writing expert, sung and played by ACE-Step 1.5 on this PC (its own install in AiProjects\\ace-step,
run as a local server, see wb_song.py). Stories: the writing expert, or an online AI for long ones
(wb_ai.ask). Movies: the writing expert writes a screenplay, each scene is painted, narrated by the
Piper voice and given music, then the scenes are put together with slow camera moves (wb_movie.py).
Every creation is saved with a .json beside it that says what was asked, who made it, where it ran
and what was skipped on the way.

The local models download on first use into AiProjects\\.hf (SDXL-Turbo ~7 GB, MusicGen ~2 GB).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import wb_ai
import wb_settings
from wb_settings import Settings

os.environ.setdefault("HF_HOME", str(wb_settings.ROOT / ".hf"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

IMAGE_MODEL = "stabilityai/sdxl-turbo"
MUSIC_MODEL = "facebook/musicgen-small"
SIZES = {"Square": (512, 512, "1024x1024"), "Wide": (768, 512, "1536x1024"), "Tall": (512, 768, "1024x1536")}
_lock = threading.RLock()  # one GPU job at a time (a movie holds it while it paints and composes)
_pipes: dict[str, object] = {}


@dataclass
class Made:
    path: Path
    kind: str
    request: str
    by: str
    where: str
    notes: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def credit(self) -> str:
        line = f"Made by {self.by} {self.where}"
        if self.notes:
            line += "\n" + "\n".join(f"– {n}" for n in self.notes)
        return line

    def save_record(self) -> None:
        data = asdict(self)
        data["path"] = self.path.name
        self.path.with_suffix(self.path.suffix + ".json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_record(path: Path) -> Made | None:
    rec = path.with_suffix(path.suffix + ".json")
    try:
        d = json.loads(rec.read_text(encoding="utf-8"))
        d["path"] = path
        return Made(**d)
    except (OSError, ValueError, TypeError):
        return None


def folder(s: Settings, kind: str) -> Path:
    p = Path(s.creations_dir) / {"image": "Images", "music": "Music", "story": "Stories", "song": "Songs",
                                 "movie": "Movies"}[kind]
    p.mkdir(parents=True, exist_ok=True)
    return p


def _slug(text: str, n: int = 40) -> str:
    import unicodedata
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:n] or "untitled"


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def free_gpu() -> list[str]:
    """Unload Ollama's models so a picture or song has the graphics memory. Returns what was unloaded."""
    import ollama
    gone = []
    try:
        for m in ollama.ps().models:
            ollama.generate(model=m.model, prompt="", keep_alive=0)
            gone.append(m.model)
    except Exception:  # noqa: BLE001 - Ollama not running: nothing to free
        pass
    return gone


def cuda_ready() -> tuple[bool, str]:
    try:
        import torch
    except ImportError:
        return False, "PyTorch is not installed (see requirements-create.txt)"
    if not torch.cuda.is_available():
        return False, "no CUDA graphics card found by PyTorch"
    return True, torch.cuda.get_device_name(0)


# ---- pictures ---------------------------------------------------------------------------------

def image_prompt(s: Settings, idea: str) -> tuple[str, str]:
    """An English, detailed picture prompt from a short idea in any language, and who wrote it."""
    msgs = [{"role": "system", "content": "You write prompts for an image generator. Reply with ONE English prompt "
             "of 20-45 words: subject, setting, style, lighting, colours. No quotes, no explanation."},
            {"role": "user", "content": idea}]
    a = wb_ai.ask(s, msgs, expert="writing", big=False, temperature=0.7)
    text = a.text.strip().strip('"').splitlines()[0] if a.text.strip() else idea
    return text, a.by


def _image_pipe():
    if "image" not in _pipes:
        import torch
        from diffusers import AutoPipelineForText2Image
        pipe = AutoPipelineForText2Image.from_pretrained(IMAGE_MODEL, torch_dtype=torch.float16, variant="fp16")
        pipe.enable_model_cpu_offload()  # parts move to the GPU only while they work: fits beside other apps
        pipe.set_progress_bar_config(disable=True)
        _pipes["image"] = pipe
    return _pipes["image"]


def make_image(s: Settings, idea: str, size: str = "Square", improve: bool = True, online: bool | None = None,
               seed: int | None = None, emit=lambda m: None) -> Made:
    """online: True = try online first, False = never, None = per the online AI setting (not Off)."""
    notes: list[str] = []
    prompt = idea
    if improve:
        emit("Writing a detailed picture prompt…")
        prompt, writer = image_prompt(s, idea)
        notes.append(f"prompt written from your idea by {writer} on this PC: “{prompt}”")
    out = folder(s, "image") / f"{_stamp()}-{_slug(idea)}.png"
    data, by, where, details = paint(s, prompt, size, online, notes, seed, emit)
    out.write_bytes(data)
    made = Made(out, "image", idea, by, where, notes, {"prompt": prompt, **details})
    made.save_record()
    return made


def paint(s: Settings, prompt: str, size: str = "Square", online: bool | None = None, notes: list[str] | None = None,
          seed: int | None = None, emit=lambda m: None) -> tuple[bytes, str, str, dict]:
    """One picture as PNG bytes: online while an image AI has tokens (if allowed), else SDXL-Turbo here.
    Returns (png, who made it, where, details) and adds what happened on the way to notes."""
    import io
    notes = notes if notes is not None else []
    w, h, online_size = SIZES.get(size, SIZES["Square"])
    try_online = online if online is not None else s.online_mode != "off"
    if try_online and wb_ai.any_image_provider(s):
        emit("Asking the online image AI…")
        png, by, more = wb_ai.online_image(s, prompt, online_size)
        notes += more
        if png:
            return png, by.rsplit(" online", 1)[0], "online" + by.rsplit(" online", 1)[1], {"size": online_size}
        notes.append("no online image AI had tokens left, so the picture was made on this PC")
    ok, why = cuda_ready()
    if not ok:
        raise RuntimeError(f"Can't make pictures on this PC: {why}.")
    with _lock:
        unloaded = free_gpu()
        if unloaded:
            notes.append(f"unloaded {', '.join(unloaded)} from the graphics card to make room")
        emit("Loading SDXL-Turbo (the first time downloads ~7 GB)…" if "image" not in _pipes else "Painting…")
        import torch
        pipe = _image_pipe()
        emit("Painting…")
        seed = seed if seed is not None else int(time.time()) % 2**31
        t = time.time()
        img = pipe(prompt=prompt, num_inference_steps=4, guidance_scale=0.0, width=w, height=h,
                   generator=torch.Generator("cpu").manual_seed(seed)).images[0]
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return (buf.getvalue(), f"SDXL-Turbo ({IMAGE_MODEL})", f"on this PC ({why}, {time.time() - t:.0f} s)",
            {"size": f"{w}x{h}", "seed": seed, "steps": 4})


# ---- music ------------------------------------------------------------------------------------

def _music():
    if "music" not in _pipes:
        import torch
        from transformers import AutoProcessor, MusicgenForConditionalGeneration
        proc = AutoProcessor.from_pretrained(MUSIC_MODEL)
        model = MusicgenForConditionalGeneration.from_pretrained(MUSIC_MODEL)
        _pipes["music"] = (proc, model)
    import torch
    proc, model = _pipes["music"]
    model.to("cuda" if torch.cuda.is_available() else "cpu")  # make_room() may have moved it out
    return proc, model


def make_room() -> list[str]:
    """Empty the graphics card for a big job (moving movie scenes): Ollama's models, MusicGen and an
    idle song server all leave it. Without this the video model spilled into shared memory and one
    scene took over 20 minutes instead of about one. Returns what was freed."""
    gone = free_gpu()
    if "music" in _pipes:
        _pipes["music"][1].to("cpu")
        gone.append("MusicGen")
    try:
        import wb_song
        if wb_song._server is not None and wb_song._server.poll() is None:
            wb_song.stop_server()
            gone.append("the song maker")
    except ImportError:
        pass
    try:
        import gc

        import torch
        gc.collect()
        torch.cuda.empty_cache()
    except ImportError:
        pass
    return gone


def music_prompt(s: Settings, idea: str) -> tuple[str, str]:
    msgs = [{"role": "system", "content": "You describe music for a music generator. Reply with ONE English line of "
             "10-30 words: genre, mood, instruments, tempo (BPM). No quotes, no explanation."},
            {"role": "user", "content": idea}]
    a = wb_ai.ask(s, msgs, expert="writing", big=False, temperature=0.7)
    text = a.text.strip().strip('"').splitlines()[0] if a.text.strip() else idea
    return text, a.by


def make_music(s: Settings, idea: str, seconds: int = 15, improve: bool = True, emit=lambda m: None) -> Made:
    import soundfile as sf
    notes: list[str] = []
    prompt = idea
    if improve:
        emit("Describing the music…")
        prompt, writer = music_prompt(s, idea)
        notes.append(f"description written from your idea by {writer} on this PC: “{prompt}”")
    notes.append("no online music AI is available, so it was made on this PC")
    seconds = max(3, min(int(seconds), 30))
    out = folder(s, "music") / f"{_stamp()}-{_slug(idea)}.wav"
    t = time.time()
    wave, rate, device = compose(prompt, seconds, notes, emit)
    sf.write(out, wave, rate)
    made = Made(out, "music", idea, f"MusicGen small ({MUSIC_MODEL}, Meta)",
                f"on this PC ({device}, {time.time() - t:.0f} s)", notes,
                {"description": prompt, "seconds": seconds, "sample_rate": rate})
    made.save_record()
    return made


def compose(prompt: str, seconds: int, notes: list[str] | None = None, emit=lambda m: None):
    """MusicGen on this PC: (wave as float32 numpy, sample rate, "GPU"/"CPU"). At most 30 seconds."""
    import numpy as np
    notes = notes if notes is not None else []
    seconds = max(3, min(int(seconds), 30))
    with _lock:
        unloaded = free_gpu()
        if unloaded:
            notes.append(f"unloaded {', '.join(unloaded)} from the graphics card to make room")
        emit("Loading MusicGen (the first time downloads ~2 GB)…" if "music" not in _pipes else "Composing…")
        proc, model = _music()
        emit(f"Composing {seconds} seconds of music…")
        inputs = proc(text=[prompt], padding=True, return_tensors="pt").to(model.device)
        audio = model.generate(**inputs, do_sample=True, guidance_scale=3.0, max_new_tokens=int(seconds * 50))
        rate = model.config.audio_encoder.sampling_rate
        wave = audio[0, 0].float().cpu().numpy()
        wave = wave / max(1e-6, float(np.abs(wave).max())) * 0.9
    return wave.astype("float32"), rate, "GPU" if str(model.device).startswith("cuda") else "CPU"


# ---- stories ----------------------------------------------------------------------------------

LENGTHS = {"Short (≈500 words)": 500, "Medium (≈1 500 words)": 1500, "Long (≈3 000 words)": 3000}


def make_story(s: Settings, idea: str, genre: str = "", length: str = "Medium (≈1 500 words)",
               language: str = "English", online: bool | None = None, cover: bool = False,
               emit=lambda m: None) -> Made:
    words = LENGTHS.get(length, 1500)
    system = (f"You are a skilled storyteller. Write an original {genre + ' ' if genre else ''}story in {language}, "
              f"about {words} words, from the user's idea. Give it a title as a Markdown '# ' heading, then the "
              "story in paragraphs. Show, don't tell; give characters their own voices; end in a satisfying way. "
              "Only the story: no notes before or after it.")
    big = True if words >= 3000 else online
    emit("Writing the story…" + (" (an online AI may take this longer one)" if big else ""))
    a = wb_ai.ask(s, [{"role": "system", "content": system}, {"role": "user", "content": idea}],
                  task="story", expert="writing", big=big, temperature=0.9, max_tokens=8000)
    text = a.text.strip()
    title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), idea[:60])
    out = folder(s, "story") / f"{_stamp()}-{_slug(title)}.md"
    notes = list(a.notes)
    details = {"genre": genre, "length": length, "language": language, "words": len(text.split())}
    made = Made(out, "story", idea, a.by, a.where, notes, details)
    if cover:
        try:
            emit("Painting a cover picture…")
            pic = make_image(s, f"Book cover illustration for a {genre} story titled '{title}': {idea}",
                             size="Tall", online=online, emit=emit)
            details["cover"] = pic.path.name
            text = f"![Cover]({pic.path.as_uri()})\n\n{text}"
            notes.append(f"cover picture made by {pic.by} {pic.where}")
        except Exception as e:  # noqa: BLE001 - the story is still worth keeping
            notes.append(f"no cover picture: {e}")
    out.write_text(f"{text}\n\n---\n*{made.credit().replace(chr(10), '  ' + chr(10))}*\n", encoding="utf-8")
    made.save_record()
    return made


# ---- voice tools ------------------------------------------------------------------------------

_settings: Settings | None = None
_on_made = None  # set by the Create tab: shows new creations there


def configure(s: Settings, on_made=None) -> None:
    global _settings, _on_made
    _settings = s
    if on_made:
        _on_made = on_made


def _done(m: Made) -> str:
    if _on_made:
        _on_made(m)
    import wb_common
    wb_common.open_path(m.path)
    return f"Saved {m.path.name} and opened it. {m.credit()}"


def create_image(idea: str) -> str:
    """Make a picture from a description and open it. Say who made it (online AI or this PC).

    Args:
        idea: what the picture should show, in any language, e.g. "a fox reading a book by a campfire"
    """
    return _done(make_image(_settings or Settings.load(), idea))


def create_music(idea: str, seconds: int = 15) -> str:
    """Make a short piece of music from a description and play it.

    Args:
        idea: genre, mood or instruments, e.g. "calm lo-fi beat with piano"
        seconds: length, 3 to 30
    """
    return _done(make_music(_settings or Settings.load(), idea, int(seconds)))


def write_story(idea: str, language: str = "English") -> str:
    """Write a short story from an idea, save it and open it. Afterwards tell the user its title and
    in one sentence what happens; don't read the whole story aloud unless asked.

    Args:
        idea: what the story is about
        language: "English" or "Swedish"
    """
    m = make_story(_settings or Settings.load(), idea, length="Short (≈500 words)",
                   language="Swedish" if language.lower().startswith(("sv", "swe")) else "English")
    text = m.path.read_text(encoding="utf-8")
    return _done(m) + f"\n\nThe story:\n{text[:3000]}"


def _in_background(kind: str, work) -> None:
    """Movies and songs take minutes: run them apart from the voice turn, open the result when done."""
    def run():
        try:
            _done(work())
        except Exception:  # noqa: BLE001 - nobody waits for this thread: log it (stderr = workbench.log)
            import traceback
            print(f"[{datetime.now():%H:%M:%S}] making a {kind} failed:", flush=True)
            traceback.print_exc()
    threading.Thread(target=run, name=f"create-{kind}", daemon=True).start()


def make_movie(idea: str, language: str = "English") -> str:
    """Start making a short narrated movie (painted scenes, a narrator's voice, music) from an idea.
    It takes a few minutes and opens by itself when it is ready; tell the user that.

    Args:
        idea: what the movie is about, e.g. "a lighthouse keeper who befriends a whale"
        language: language of the narration, "English" or "Swedish"
    """
    import wb_movie
    lang = "Swedish" if language.lower().startswith(("sv", "swe")) else "English"
    _in_background("movie", lambda: wb_movie.make_movie(_settings or Settings.load(), idea, language=lang))
    return "Started the movie. It takes a few minutes and opens by itself when it's ready (also in Create → Movie)."


def make_song(idea: str, language: str = "English") -> str:
    """Start making a song with singing: lyrics are written, then it is sung and played by ACE-Step on
    this PC. It takes a minute or two and plays by itself when it is ready; tell the user that.

    Args:
        idea: what the song is about and its style, e.g. "a happy summer pop song about the archipelago"
        language: language of the lyrics, "English" or "Swedish"
    """
    import wb_song
    lang = "Swedish" if language.lower().startswith(("sv", "swe")) else "English"
    _in_background("song", lambda: wb_song.make_song(_settings or Settings.load(), idea, language=lang))
    return "Started the song. It plays by itself when it's ready (also in Create → Song)."


TOOLS = {f.__name__: f for f in (create_image, create_music, write_story, make_movie, make_song)}
