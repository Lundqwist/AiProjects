"""Make a short narrated movie from an idea, all on this PC unless online AI is allowed.

1. The writing expert writes a screenplay as JSON: title, a look shared by every picture (style and
   how the characters look, so they stay the same), music, and 4-10 scenes (a picture prompt in
   English and one to three sentences of narration in English or Swedish).
2. Each scene is painted (wb_create.paint: online image AI while it has tokens, else SDXL-Turbo).
3. Each narration is read by the Piper voice chosen in Settings (the voice assistant's voice).
4. MusicGen makes up to 30 s of music, looped with a crossfade under the whole movie.
5. Frames are drawn with slow zoom and pan (a "Ken Burns" camera), crossfades between scenes, a title
   and subtitles, and piped into ffmpeg (from imageio-ffmpeg) as H.264 + AAC in an .mp4.

The scene pictures, narration and a .srt subtitle file are kept in a folder beside the movie.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

import wb_ai
import wb_create
from wb_settings import Settings

FPS = 24
W, H = 1280, 720
FADE = 0.8          # crossfade between scenes, seconds
LEAD = 0.5          # silence before each narration starts
TAIL = 1.2          # picture stays after the narration ends
MIN_SCENE = 4.0
LENGTHS = {"Short (4 scenes)": 4, "Medium (6 scenes)": 6, "Long (10 scenes)": 10}
STYLES = ["Cinematic film still", "Watercolour illustration", "3D animated film", "Anime", "Comic book",
          "Oil painting", "Children's picture book"]
AUDIO_RATE = 44100


# ---- 1. screenplay ------------------------------------------------------------------------------

def _json_from(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("the screenplay was not JSON")
    return json.loads(text[start:end + 1])


def screenplay(s: Settings, idea: str, scenes: int, style: str, language: str, online: bool | None) -> tuple[dict, wb_ai.Answer]:
    system = (
        "You write screenplays for short narrated picture films. Reply with JSON only, no other text:\n"
        '{"title": "<title in LANGUAGE>",\n'
        ' "look": "<English, 10-20 words: colours and light, and how each main character looks, so every '
        'picture shows them the same way>",\n'
        ' "music": "<English, 10-25 words: genre, mood, instruments, tempo, for background music>",\n'
        ' "scenes": [{"picture": "<English image prompt, 15-30 words: who is where doing what, camera angle; '
        'name the characters by their looks, never only by name>", "narration": "<1-3 short sentences in '
        'LANGUAGE, read aloud by a narrator>"}]}\n'
        "Write exactly N scenes that tell a whole story with a beginning, a middle and an end. The narration "
        "carries the story; the pictures show it. No text, letters or words in the pictures."
    ).replace("LANGUAGE", language).replace("STYLE", style).replace("exactly N", f"exactly {scenes}")
    last_error = ""
    for attempt in range(2):
        a = wb_ai.ask(s, [{"role": "system", "content": system},
                          {"role": "user", "content": idea + (f"\n\n(Last reply failed: {last_error}. JSON only.)"
                                                              if last_error else "")}],
                      task="story", expert="writing", big=online, temperature=0.8, max_tokens=4000)
        try:
            play = _json_from(a.text)
            items = [sc for sc in play.get("scenes", []) if str(sc.get("picture", "")).strip()]
            if len(items) < 2:
                raise ValueError("fewer than two scenes")
            play["scenes"] = items[:scenes + 2]
            play.setdefault("title", idea[:60])
            play.setdefault("look", style)
            play.setdefault("music", "gentle cinematic orchestral background music, 80 BPM")
            return play, a
        except (ValueError, TypeError) as e:
            last_error = str(e)
    raise RuntimeError(f"The writing expert didn't write a usable screenplay ({last_error}).")


# ---- 3. narration -------------------------------------------------------------------------------

def _voice(s: Settings, language: str):
    from piper import PiperVoice
    name = s.voice_sv if language == "Swedish" else s.voice_en
    path = Path(s.voices_dir) / f"{name}.onnx"
    if not path.exists():
        raise RuntimeError(f"The {language} voice {name} is not installed (Settings → Voice library).")
    return name, PiperVoice.load(path)


def narrate(voice, text: str, speed: float):
    """Piper speech as float32 at AUDIO_RATE."""
    import numpy as np
    from piper import SynthesisConfig
    cfg = SynthesisConfig(length_scale=1 / max(speed, 0.1))
    chunks = list(voice.synthesize(text, syn_config=cfg))
    if not chunks:
        return np.zeros(0, dtype="float32")
    pcm = np.concatenate([c.audio_int16_array for c in chunks]).astype("float32") / 32768
    return _resample(pcm, chunks[0].sample_rate)


def _resample(wave, rate: int):
    import numpy as np
    if rate == AUDIO_RATE or len(wave) == 0:
        return wave.astype("float32")
    n = int(len(wave) * AUDIO_RATE / rate)
    return np.interp(np.linspace(0, len(wave) - 1, n), np.arange(len(wave)), wave).astype("float32")


# ---- 4. music -----------------------------------------------------------------------------------

def _loop_to(wave, length: int, cross: int = AUDIO_RATE * 2):
    """Repeat music to `length` samples with crossfades, fading in and out."""
    import numpy as np
    if len(wave) == 0:
        return np.zeros(length, dtype="float32")
    out = wave.copy()
    cross = min(cross, len(wave) // 3)
    while len(out) < length:
        ramp = np.linspace(0, 1, cross, dtype="float32")
        out[-cross:] = out[-cross:] * (1 - ramp) + wave[:cross] * ramp
        out = np.concatenate([out, wave[cross:]])
    out = out[:length]
    fade_in, fade_out = min(AUDIO_RATE, length // 4), min(AUDIO_RATE * 3, length // 3)
    out[:fade_in] *= np.linspace(0, 1, fade_in, dtype="float32")
    out[-fade_out:] *= np.linspace(1, 0, fade_out, dtype="float32")
    return out


# ---- 5. pictures in motion ----------------------------------------------------------------------

def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    for name in (["seguisb.ttf", "segoeuib.ttf"] if bold else ["segoeui.ttf"]) + ["arial.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw, text: str, font, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        test = f"{line} {word}".strip()
        if draw.textlength(test, font=font) <= width or not line:
            line = test
        else:
            lines.append(line)
            line = word
    return lines + ([line] if line else [])


def _subtitle(text: str):
    """A transparent 1280x720 layer with the narration at the bottom."""
    from PIL import Image, ImageDraw
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    font = _font(30)
    lines = _wrap(d, text, font, W - 240)[:4]
    if not lines:
        return layer
    lh = 40
    top = H - 50 - lh * len(lines)
    box_w = max(d.textlength(l, font=font) for l in lines) + 40
    d.rounded_rectangle(((W - box_w) / 2, top - 12, (W + box_w) / 2, H - 38), 10, fill=(8, 18, 36, 165))
    for i, l in enumerate(lines):
        d.text((W / 2, top + i * lh), l, font=font, fill=(238, 242, 248, 255), anchor="ma")
    return layer


def _title_card(title: str):
    from PIL import Image, ImageDraw
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    font = _font(64, bold=True)
    lines = _wrap(d, title, font, W - 200)[:3]
    top = H / 2 - 40 * len(lines)
    d.rectangle((0, top - 40, W, top + 80 * len(lines) + 30), fill=(8, 18, 36, 140))
    for i, l in enumerate(lines):
        d.text((W / 2, top + i * 80), l, font=font, fill=(212, 175, 55, 255), anchor="ma")
    return layer


class Shot:
    """One scene's picture with its camera move: zoom in or out, panning one way."""

    def __init__(self, img, index: int):
        from PIL import Image
        img = img.convert("RGB")
        scale = max(W * 1.25 / img.width, H * 1.25 / img.height)
        self.img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        self.zoom_in = index % 2 == 0
        self.pan = [(-1, 0), (1, 0), (0, -1), (1, 1), (-1, 1)][index % 5]

    def frame(self, t: float):
        """t from 0 to 1 over the scene."""
        from PIL import Image
        t = min(max(t, 0.0), 1.0)
        ease = t * t * (3 - 2 * t)
        z = 1.0 + 0.16 * (ease if self.zoom_in else 1 - ease)
        iw, ih = self.img.size
        base_w = min(iw, ih * W / H)
        cw, ch = base_w / z, base_w / z * H / W
        room_x, room_y = (iw - cw) / 2, (ih - ch) / 2
        cx = iw / 2 + self.pan[0] * room_x * 0.8 * (ease - 0.5)
        cy = ih / 2 + self.pan[1] * room_y * 0.8 * (ease - 0.5)
        box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
        return self.img.transform((W, H), Image.EXTENT, box, Image.BILINEAR)


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
    except ImportError as e:
        raise RuntimeError("imageio-ffmpeg is not installed (see requirements-create.txt).") from e
    return imageio_ffmpeg.get_ffmpeg_exe()


def render(shots: list[Shot], starts: list[float], durs: list[float], subs: list, title_layer, audio_path: Path,
           out: Path, emit=lambda m: None) -> None:
    from PIL import Image
    total = starts[-1] + durs[-1]
    frames = int(total * FPS)
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-i", str(audio_path), "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=0x08000000)
    black = Image.new("RGB", (W, H))

    def scene_frame(i: int, tau: float):
        im = shots[i].frame((tau - starts[i]) / durs[i])
        if subs[i] is not None:
            im = Image.alpha_composite(im.convert("RGBA"), subs[i]).convert("RGB")
        return im

    try:
        for f in range(frames):
            tau = f / FPS
            live = [i for i in range(len(shots)) if starts[i] <= tau < starts[i] + durs[i]]
            if not live:
                live = [len(shots) - 1]
            im = scene_frame(live[-1], tau)
            if len(live) > 1:  # crossfade into the next scene
                a = min(1.0, (tau - starts[live[-1]]) / FADE)
                im = Image.blend(scene_frame(live[0], tau), im, a)
            if title_layer is not None and tau < 4.0:
                alpha = min(1.0, tau / 0.6, (4.0 - tau) / 0.8)
                layer = title_layer.copy()
                layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
                im = Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB")
            edge = min(tau / 0.8, (total - tau) / 1.2, 1.0)  # fade from and to black
            if edge < 1.0:
                im = Image.blend(black, im, max(edge, 0.0))
            proc.stdin.write(im.tobytes())
            if f % (FPS * 2) == 0:
                emit(f"Filming… {f * 100 // frames} %")
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="replace")
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed: {err.strip()[-300:]}")
    finally:
        if proc.poll() is None:
            proc.kill()


def _srt(starts, narr_lens, texts) -> str:
    def ts(x):
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"
    blocks = []
    for n, (st, ln, tx) in enumerate(zip(starts, narr_lens, texts), 1):
        if tx:
            blocks.append(f"{n}\n{ts(st + LEAD)} --> {ts(st + LEAD + max(ln, 1.5))}\n{tx}\n")
    return "\n".join(blocks)


# ---- the whole movie ----------------------------------------------------------------------------

def make_movie(s: Settings, idea: str, length: str = "Short (4 scenes)", style: str = STYLES[0],
               language: str = "English", subtitles: bool = True, music: bool = True, online: bool | None = None,
               emit=lambda m: None) -> wb_create.Made:
    import io

    import numpy as np
    import soundfile as sf
    from PIL import Image

    t0 = time.time()
    count = LENGTHS.get(length, 4)
    emit("Writing the screenplay…")
    play, writer = screenplay(s, idea, count, style, language, online)
    notes = [f"screenplay written by {writer.by} {writer.where}"] + list(writer.notes)
    stem = f"{wb_create._stamp()}-{wb_create._slug(play['title'])}"
    out = wb_create.folder(s, "movie") / f"{stem}.mp4"
    work = out.with_name(stem + "-scenes")
    work.mkdir(parents=True, exist_ok=True)
    (work / "screenplay.json").write_text(json.dumps(play, indent=2, ensure_ascii=False), encoding="utf-8")

    with wb_create._lock:  # paint, compose and narrate without another GPU job in between
        painters, shots = [], []
        seed = int(time.time()) % 2**30
        for i, sc in enumerate(play["scenes"]):
            emit(f"Painting scene {i + 1} of {len(play['scenes'])}…")
            # SDXL reads only ~77 tokens: style first, then the scene, then the shared look (may be cut short)
            prompt = f"{style}. {sc['picture']}. {play['look']}"
            png, by, where, _ = wb_create.paint(s, prompt, "Wide", online, notes, seed=seed + i)
            (work / f"scene-{i + 1:02}.png").write_bytes(png)
            painters.append((by, where.split(" (")[0]))
            shots.append(Shot(Image.open(io.BytesIO(png)), i))

        emit("Recording the narrator…")
        voice_name, voice = _voice(s, language)
        speech = [narrate(voice, str(sc.get("narration", "")).strip(), s.speech_speed) if sc.get("narration")
                  else np.zeros(0, dtype="float32") for sc in play["scenes"]]
        narr_lens = [len(w) / AUDIO_RATE for w in speech]
        durs = [max(MIN_SCENE, LEAD + ln + TAIL) for ln in narr_lens]
        starts = [0.0]
        for d in durs[:-1]:
            starts.append(starts[-1] + d - FADE)
        total = starts[-1] + durs[-1]
        n = int(total * AUDIO_RATE) + AUDIO_RATE

        voice_track = np.zeros(n, dtype="float32")
        for st, w in zip(starts, speech):
            a = int((st + LEAD) * AUDIO_RATE)
            voice_track[a:a + len(w)] += w[:n - a]
        mix = voice_track * 0.95
        music_desc = ""
        if music:
            music_desc = str(play.get("music", ""))
            wave, rate, device = wb_create.compose(music_desc, min(30, int(total) + 2), notes, emit)
            bed = _loop_to(_resample(wave, rate), n)
            talking = np.convolve((np.abs(voice_track) > 0.01).astype("float32"),
                                  np.ones(AUDIO_RATE // 2, dtype="float32") / (AUDIO_RATE // 2), mode="same")
            mix += bed * (0.32 - 0.18 * np.clip(talking * 4, 0, 1))  # quieter while the narrator speaks
            notes.append(f"music by MusicGen small ({wb_create.MUSIC_MODEL}, Meta) on this PC ({device})")
        peak = float(np.abs(mix).max()) or 1.0
        mix = mix / max(peak, 1.0) * 0.95
        audio_path = work / "soundtrack.wav"
        sf.write(audio_path, mix, AUDIO_RATE)

    texts = [str(sc.get("narration", "")).strip() for sc in play["scenes"]]
    (out.with_suffix(".srt")).write_text(_srt(starts, narr_lens, texts), encoding="utf-8")
    subs = [_subtitle(tx) if subtitles and tx else None for tx in texts]
    emit("Filming…")
    render(shots, starts, durs, subs, _title_card(play["title"]), audio_path, out, emit)

    notes.insert(1, f"{len(shots)} scenes painted by {', '.join(sorted({f'{b} {w}' for b, w in painters}))}")
    notes.append(f"narrated by the Piper voice {voice_name} on this PC")
    notes.append(f"put together with ffmpeg on this PC: {total:.0f} s, {W}x{H}, {FPS} fps")
    makers = [writer.by] + sorted({b.split(" (")[0] for b, _ in painters}) + ["Piper"] + (["MusicGen"] if music else [])
    local = writer.where == "on this PC" and all(w.startswith("on this PC") for _, w in painters)
    made = wb_create.Made(out, "movie", idea, ", ".join(makers[:-1]) + " and " + makers[-1],
                          f"{'on this PC' if local else 'on this PC and online'} ({time.time() - t0:.0f} s in all)",
                          notes, {"title": play["title"], "style": style, "language": language,
                                  "seconds": round(total, 1), "scenes": play["scenes"], "look": play["look"],
                                  "music": music_desc, "voice": voice_name, "folder": work.name})
    made.save_record()
    return made
