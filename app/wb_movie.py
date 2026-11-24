"""Make a short narrated movie from an idea, all on this PC unless online AI is allowed.

1. The writing expert writes a screenplay as JSON: title, a look shared by every picture (style and
   how the characters look, so they stay the same), music, and 4-10 scenes (a picture prompt in
   English and one to three sentences of narration in English or Swedish).
2. Each scene is painted (wb_create.paint: online image AI while it has tokens, else SDXL-Turbo).
3. Each narration is read by the Piper voice chosen in Settings (the voice assistant's voice).
4. MusicGen makes up to 30 s of music, looped with a crossfade under the whole movie.
5. Each painted scene is brought to life by LTX-Video (wb_video.py): real motion, as long as the
   narration needs. Without that model (or when "Moving scenes" is off) the pictures get a slow
   zoom and pan instead (a "Ken Burns" camera).
6. Crossfades between scenes, a title and subtitles are added and the frames piped into ffmpeg (from
   imageio-ffmpeg) as H.264 + AAC in an .mp4.

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


SILENT_WORDS = re.compile(r"\b(silent (movie|film)|stumfilm|no narrat\w*|without (a )?narrat\w*|no voice|"
                          r"utan (berättar\w*|röst|speaker)|no talking)\b", re.I)


def screenplay(s: Settings, idea: str, scenes: int, style: str, language: str, online: bool | None) -> tuple[dict, wb_ai.Answer]:
    """The writer first lists what the request asks for, then keeps to it. Before this, a small model
    dropped details ("undead priest" became a woman with silver hair), invented others (new characters,
    a flower, a backstory), and couldn't make a silent film because the narrator was always added."""
    system = (
        "You write screenplays for short picture films. Reply with JSON only, no other text:\n"
        '{"wishes": [{"wish": "<each specific thing the request asks for, a short English phrase>", '
        '"scenes": [<numbers of the scenes that show it, or [] for things about the whole film>]}],\n'
        ' "silent": <true only if the request asks for a silent film, no narrator or no voice; else false>,\n'
        ' "title": "<title in LANGUAGE>",\n'
        ' "setting": "<English, at most 12 words: the world, game or place and the time the request gives>",\n'
        ' "characters": [{"name": "<name>", "looks": "<English, 10-18 words: species or race FIRST, then body, '
        'face, hair, clothes, held items>"}],\n'
        ' "look": "<English, at most 12 words: colours, light and art direction for every picture>",\n'
        ' "music": "<English, 10-25 words: genre, mood, instruments, tempo, for background music>",\n'
        ' "scenes": [{"who": ["<names of the characters in this scene>"], "picture": "<English, 12-25 words: '
        'what happens where, and the camera angle>", "motion": "<English, 10-20 words: what moves during the '
        'scene and one simple camera move>", "narration": "<1-3 short sentences in LANGUAGE read by a '
        'narrator; if silent, the text of a title card: one short sentence of 6-12 words in LANGUAGE that tells '
        'what happens>"}]}\n'
        "Rules:\n"
        "1. Stay true to the request. Everything it asks for must be in the film; list each wish and the "
        "scenes that show it. Keep its names, its words for what a character is (undead, zombie, orc, robot, "
        "child...), the world or game it names, and the events it describes. A character described as undead "
        "or a zombie IS undead and must look it (e.g. grey rotting skin, glowing eyes); never turn that into "
        "something else or into enemies.\n"
        "2. Invent only what the story needs to fill gaps, and keep it plain. No extra main characters, "
        "subplots, backstories or objects the request doesn't suggest. If it mentions a group, give the group "
        "two or three members named by their role (the warrior, the rogue), not new names.\n"
        "3. If the request names a game, book or world, keep to it; if it names none, don't name one. Don't "
        "name places: describe them plainly (a misty lake in a dead grey forest, a torch-lit crypt), in a way "
        "that fits the character's kind and home.\n"
        "4. Write exactly N scenes with a beginning, a middle and an end. No text, letters or words in the "
        "pictures."
    ).replace("LANGUAGE", language).replace("exactly N", f"exactly {scenes}")
    last_error = ""
    for attempt in range(3):
        a = wb_ai.ask(s, [{"role": "system", "content": system},
                          {"role": "user", "content": f"The request:\n{idea}"
                           + (f"\n\n(Your last reply had a problem: {last_error}. Fix it. JSON only.)"
                              if last_error else "")}],
                      task="story", expert="writing", big=online, temperature=0.6, max_tokens=5000)
        try:
            play = _json_from(a.text)
            items = [sc for sc in play.get("scenes", []) if str(sc.get("picture", "")).strip()]
            if len(items) < 2:
                raise ValueError("fewer than two scenes")
            if not play.get("characters") and attempt < 2:
                raise ValueError("no characters with their looks")
            missing = [str(w.get("wish", "")) for w in play.get("wishes", [])
                       if isinstance(w, dict) and w.get("scenes") == [] and not _film_wide(str(w.get("wish", "")))]
            if missing and attempt < 2:
                raise ValueError(f"these wishes are in no scene: {'; '.join(missing)}")
            play["scenes"] = items[:scenes + 2]
            for sc in play["scenes"]:  # 'Title card: "Wounded"' -> 'Wounded'
                sc["narration"] = re.sub(r"^\s*(title card|intertitle|card)\s*:\s*", "", str(sc.get("narration", "")),
                                         flags=re.I).strip().strip("'\"“”‘’")
            # silent only when the request asks: the writer once chose it by itself for a paper boat story
            asked = [str(w.get("wish", "")) for w in play.get("wishes", []) if isinstance(w, dict)]
            play["silent"] = bool(SILENT_WORDS.search(idea)) or (
                bool(play.get("silent")) and any(re.search(r"silent|narrat|voice|talk", w, re.I) for w in asked))
            play.setdefault("title", idea[:60])
            play.setdefault("look", style)
            play.setdefault("setting", "")
            play.setdefault("music", "gentle cinematic orchestral background music, 80 BPM")
            return play, a
        except (ValueError, TypeError, AttributeError) as e:
            last_error = str(e)
    raise RuntimeError(f"The writing expert didn't write a usable screenplay ({last_error}).")


def _film_wide(wish: str) -> bool:
    """Wishes about the whole film rather than a scene (silent, style, music, length, language)."""
    return bool(re.search(r"silent|style|music|sound|narrat|voice|language|swedish|english|long|short|colou?r|"
                          r"black and white|animated|anime|cartoon|title|subtitle", wish, re.I))


def _chars(play: dict) -> list[dict]:
    return [c for c in play.get("characters", []) if isinstance(c, dict) and c.get("looks") and c.get("name")]


def _tag(c: dict) -> str:
    """A short description that stands in for a name: the first part of the looks, which the screenplay
    asks to be the species or race ("undead female elf", "dwarf"), at most three words."""
    first = re.split(r"[,;.]", str(c["looks"]).strip())[0]
    return " ".join(first.split()[:3])


def _scene_text(play: dict, sc: dict, field: str) -> tuple[str, str]:
    """The scene's picture (or motion) text with names swapped for looks, since picture models don't know
    names, and the full looks of the scene's main character, which go first in the prompt."""
    chars = _chars(play)
    who = [str(n).lower() for n in sc.get("who", []) if n]
    text = str(sc.get(field) or "").strip().rstrip(".")
    in_scene = [c for c in chars if str(c["name"]).lower() in who or str(c["name"]).split()[0].lower() in text.lower()]
    lead = (in_scene or chars[:1] or [None])[0]
    for c in sorted(chars, key=lambda c: -len(str(c["name"]))):
        if str(c["name"]).lower().startswith("the "):  # a role ("the warrior") already says what to paint
            continue
        for name in {str(c["name"]), str(c["name"]).split()[0]}:
            text = re.sub(rf"\b{re.escape(name)}\b", "the " + _tag(c), text)
    return text, (str(lead["looks"]).strip().rstrip(".") if lead else "")


def picture_prompt(play: dict, sc: dict, style: str) -> str:
    # SDXL reads only ~77 tokens: style, the main character's full looks, the scene; setting and look last
    scene, looks = _scene_text(play, sc, "picture")
    return ". ".join(p for p in (style, looks, scene, play.get("setting", ""), play.get("look", "")) if p)


def video_prompt(play: dict, sc: dict, style: str) -> str:
    """The video model's T5 encoder reads 128 tokens. The main character's looks go right after the
    style and are said to stay: with them after the motion, LTX smoothed an undead priest's rotting face
    into a normal woman's within two seconds. (The distilled model runs without guidance, so the negative
    prompt has no effect: what must stay has to be in this prompt.)"""
    motion, looks = _scene_text(play, sc, "motion")
    scene, _ = _scene_text(play, sc, "picture")
    keep = f"The {looks} keeps exactly this look in every frame" if looks else ""
    return ". ".join(p for p in (f"{style}, smooth motion", keep,
                                 motion or "gentle natural motion, slow camera push in", scene,
                                 play.get("setting", "")) if p)


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


class MovingShot:
    """One scene as video frames from LTX-Video, cropped to 16:9 and scaled to the movie size."""

    def __init__(self, frames: list):
        self.frames = frames

    def frame(self, t: float):
        from PIL import Image
        t = min(max(t, 0.0), 1.0)
        im = self.frames[min(len(self.frames) - 1, int(t * len(self.frames)))]
        crop = round(im.width * H / W)
        top = (im.height - crop) // 2
        return im.crop((0, top, im.width, top + crop)).resize((W, H), Image.BICUBIC)


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


def _save_clip(frames: list, path: Path) -> None:
    """One scene's raw clip (no sound) beside the scene picture, to reuse or look at."""
    w, h = frames[0].size
    r = subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                        "-s", f"{w}x{h}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "20",
                        "-pix_fmt", "yuv420p", str(path)],
                       input=b"".join(f.convert("RGB").tobytes() for f in frames), capture_output=True,
                       creationflags=0x08000000)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg couldn't save {path.name}: {r.stderr.decode(errors='replace')[-200:]}")


def _srt(starts, narr_lens, texts) -> str:
    def ts(x):
        ms = int(round(x * 1000))
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"
    blocks = []
    for n, (st, ln, tx) in enumerate(zip(starts, narr_lens, texts), 1):
        if tx:
            blocks.append(f"{n}\n{ts(st + LEAD)} --> {ts(st + LEAD + max(ln, 1.5))}\n{tx}\n")
    return "\n".join(blocks)


# ---- the screenplay as editable text ------------------------------------------------------------

def write_screenplay(s: Settings, idea: str, length: str = "Short (4 scenes)", style: str = STYLES[0],
                     language: str = "English", online: bool | None = None, emit=lambda m: None) -> dict:
    """Only the screenplay, to check and edit before filming. Who wrote it is kept in _by/_where/_notes."""
    emit("Writing the screenplay…")
    play, writer = screenplay(s, idea, LENGTHS.get(length, 4), style, language, online)
    play.update({"_by": writer.by, "_where": writer.where, "_notes": list(writer.notes)})
    return play


YES = {"yes", "y", "true", "ja", "on", "1"}
FIELDS = {"who": "Who", "picture": "Picture", "motion": "Motion", "narration": "Narration"}


def play_to_text(play: dict) -> str:
    """The screenplay as plain text that is easy to read and change."""
    lines = [f"Title: {play.get('title', '')}",
             f"Silent: {'yes' if play.get('silent') else 'no'}",
             f"Setting: {play.get('setting', '')}",
             f"Look: {play.get('look', '')}",
             f"Music: {play.get('music', '')}", ""]
    for c in play.get("characters", []):
        if isinstance(c, dict):
            lines.append(f"Character: {c.get('name', '')} | {c.get('looks', '')}")
    for i, sc in enumerate(play.get("scenes", []), 1):
        lines += ["", f"Scene {i}"]
        for key, label in FIELDS.items():
            val = sc.get(key, "")
            lines.append(f"{label}: {', '.join(map(str, val)) if isinstance(val, list) else val}")
    return "\n".join(lines) + "\n"


def text_to_play(text: str, base: dict | None = None) -> dict:
    """Read the text back. Scenes can be changed, removed, added (a new "Scene" line) or moved; the
    request's wishes and who wrote it come from `base`. Raises ValueError with what is wrong."""
    play = {k: v for k, v in (base or {}).items() if k.startswith("_") or k == "wishes"}
    play.update({"characters": [], "scenes": []})
    scene = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if re.fullmatch(r"(scene|scen)\s*\d*\s*:?", line, re.I):
            scene = {"who": [], "picture": "", "motion": "", "narration": ""}
            play["scenes"].append(scene)
            continue
        key, sep, val = line.partition(":")
        if not sep:
            if scene is not None and scene.get("narration"):  # a narration over several lines
                scene["narration"] += " " + line
                continue
            raise ValueError(f"line {n} needs a label such as 'Picture:' — “{line[:50]}”")
        key, val = key.strip().lower(), val.strip()
        if scene is None:
            if key == "character":
                name, _, looks = val.partition("|")
                if not looks.strip():
                    raise ValueError(f"line {n}: write a character as 'Character: Name | how they look'")
                play["characters"].append({"name": name.strip(), "looks": looks.strip()})
            elif key == "silent":
                play["silent"] = val.lower() in YES
            elif key in ("title", "setting", "look", "music"):
                play[key] = val
            else:
                raise ValueError(f"line {n}: unknown label '{key.title()}' before the first scene")
        else:
            field = next((f for f, label in FIELDS.items() if label.lower() == key), None)
            if not field:
                raise ValueError(f"line {n}: a scene has Who, Picture, Motion and Narration, not '{key.title()}'")
            scene[field] = [w.strip() for w in val.split(",") if w.strip()] if field == "who" else val
    play["scenes"] = [sc for sc in play["scenes"] if sc["picture"]]
    if not play["scenes"]:
        raise ValueError("the screenplay needs at least one scene with a Picture line")
    play.setdefault("title", "Untitled")
    play.setdefault("silent", False)
    for k in ("setting", "look", "music"):
        play.setdefault(k, "")
    return play


# ---- the whole movie ----------------------------------------------------------------------------

def make_movie(s: Settings, idea: str, length: str = "Short (4 scenes)", style: str = STYLES[0],
               language: str = "English", subtitles: bool = True, music: bool = True, online: bool | None = None,
               emit=lambda m: None, moving: bool = True, play: dict | None = None,
               narration: bool = True) -> wb_create.Made:
    """The whole movie. With `play` (from write_screenplay, perhaps edited with text_to_play) the
    screenplay is filmed as it is; without it one is written first. narration=False: no narrator voice
    (the text is still shown if subtitles are on). A silent film (asked for in the request or "Silent:
    yes" in the screenplay) has no voice either, and shows its title cards even with subtitles off."""
    import io

    import numpy as np
    import soundfile as sf
    from PIL import Image

    import wb_video

    t0 = time.time()
    still_why = ""
    if moving:
        moving, why = wb_video.ready()
        still_why = "" if moving else f"still pictures with camera moves instead of moving scenes: {why}"
    if play is None:
        play = write_screenplay(s, idea, length, style, language, online, emit)
    writer_by, writer_where = play.get("_by", "the writing expert"), play.get("_where", "on this PC")
    notes = ([f"screenplay written by {writer_by} {writer_where}"
              + (", then checked and edited by you" if play.get("_edited") else
                 ", checked by you" if play.get("_checked") else "")]
             + list(play.get("_notes", [])) + ([still_why] if still_why else []))
    play = {k: v for k, v in play.items() if not k.startswith("_")}
    stem = f"{wb_create._stamp()}-{wb_create._slug(play['title'])}"
    out = wb_create.folder(s, "movie") / f"{stem}.mp4"
    work = out.with_name(stem + "-scenes")
    work.mkdir(parents=True, exist_ok=True)
    (work / "screenplay.json").write_text(json.dumps(play, indent=2, ensure_ascii=False), encoding="utf-8")

    with wb_create._lock:  # paint, compose and narrate without another GPU job in between
        painters, shots, pictures = [], [], []
        seed = int(time.time()) % 2**30
        for i, sc in enumerate(play["scenes"]):
            emit(f"Painting scene {i + 1} of {len(play['scenes'])}…")
            png, by, where, _ = wb_create.paint(s, picture_prompt(play, sc, style), "Wide", online, notes, seed=seed + i)
            (work / f"scene-{i + 1:02}.png").write_bytes(png)
            painters.append((by, where.split(" (")[0]))
            pictures.append(Image.open(io.BytesIO(png)).convert("RGB"))
            shots.append(Shot(pictures[-1], i))

        voiced = narration and not play["silent"]
        if not voiced:  # no narrator: each scene lasts long enough to read its text
            voice_name = ""
            speech = [np.zeros(0, dtype="float32") for _ in play["scenes"]]
            narr_lens = [max(2.5, len(str(sc.get("narration", "")).split()) * 0.4 + 1.0) for sc in play["scenes"]]
            notes.append("silent film as asked: no narrator, the text is shown on screen" if play["silent"] else
                         "narration turned off: no narrator" + (", the text is shown as subtitles" if subtitles else ""))
        else:
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

        if moving:
            t_anim = time.time()
            freed = wb_create.make_room()
            if freed:
                notes.append(f"moved {', '.join(freed)} off the graphics card for the video model")
            try:
                for i, sc in enumerate(play["scenes"]):
                    emit(f"Animating scene {i + 1} of {len(shots)} ({durs[i]:.0f} s)…")
                    frames = wb_video.animate(pictures[i], video_prompt(play, sc, style), durs[i], seed + 100 * i,
                                              lambda m, i=i: emit(f"Animating scene {i + 1} of {len(shots)} {m}"))
                    _save_clip(frames, work / f"scene-{i + 1:02}.mp4")
                    shots[i] = MovingShot(frames)
            finally:
                wb_video.unload()
            notes.append(f"scenes animated by {wb_video.MODEL} (Lightricks) on this PC "
                         f"({time.time() - t_anim:.0f} s)")

    texts = [str(sc.get("narration", "")).strip() for sc in play["scenes"]]
    (out.with_suffix(".srt")).write_text(_srt(starts, narr_lens, texts), encoding="utf-8")
    # a silent film tells its story with the text on screen, so it is shown even with subtitles off
    subs = [_subtitle(tx) if (subtitles or play["silent"]) and tx else None for tx in texts]
    emit("Filming…")
    render(shots, starts, durs, subs, _title_card(play["title"]), audio_path, out, emit)

    notes.insert(1, f"{len(shots)} scenes painted by {', '.join(sorted({f'{b} {w}' for b, w in painters}))}")
    if voiced:
        notes.append(f"narrated by the Piper voice {voice_name} on this PC")
    wishes = [str(w.get("wish")) for w in play.get("wishes", []) if isinstance(w, dict) and w.get("wish")]
    if wishes:
        notes.append("what the screenplay took from your request: " + "; ".join(wishes))
    notes.append(f"put together with ffmpeg on this PC: {total:.0f} s, {W}x{H}, {FPS} fps")
    makers = ([writer_by] + sorted({b.split(" (")[0] for b, _ in painters}) + (["LTX-Video"] if moving else [])
              + (["Piper"] if voiced else []) + (["MusicGen"] if music else []))
    local = writer_where == "on this PC" and all(w.startswith("on this PC") for _, w in painters)
    made = wb_create.Made(out, "movie", idea, ", ".join(makers[:-1]) + " and " + makers[-1],
                          f"{'on this PC' if local else 'on this PC and online'} ({time.time() - t0:.0f} s in all)",
                          notes, {"title": play["title"], "style": style, "language": language,
                                  "seconds": round(total, 1), "scenes": play["scenes"], "look": play["look"],
                                  "music": music_desc,
                                  "voice": voice_name or ("none (silent film)" if play["silent"] else "none"),
                                  "subtitles": subtitles or play["silent"],
                                  "folder": work.name, "moving": moving, "silent": play["silent"],
                                  "characters": play.get("characters", []), "setting": play.get("setting", ""),
                                  "wishes": play.get("wishes", [])})
    made.save_record()
    return made
