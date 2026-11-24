"""Piper voice library: what's installed, what's available, female/male, download and preview."""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

CATALOG_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json"
CACHE = Path(__file__).resolve().parent.parent / "02-voice" / "voices" / "catalog-cache.json"
LANGUAGES = {"en": ("en_US", "en_GB"), "sv": ("sv_SE",)}

# Piper's catalog has no gender field; these are the single-speaker voices whose speaker is known.
FEMALE = {"amy", "hfc_female", "kristin", "kathleen", "ljspeech", "lessac", "jenny_dioco", "cori",
          "alba", "southern_english_female", "alma", "lisa"}
MALE = {"alan", "northern_english_male", "bryce", "danny", "hfc_male", "joe", "john", "mike",
        "norman", "ryan", "sam", "nst"}

SAMPLE_TEXT = {"en": "Hello! I am your voice assistant. You have three tasks today.",
               "sv": "Hej! Jag är din röstassistent. Du har tre uppgifter i dag."}


def speaker(name: str) -> str:
    return name.split("-")[1] if name.count("-") >= 2 else name


def gender(name: str) -> str:
    s = speaker(name)
    return "female" if s in FEMALE else "male" if s in MALE else "several/unknown"


def label(name: str) -> str:
    """'en_US-amy-medium' -> 'Amy (female, US, medium)'."""
    parts = name.split("-")
    if len(parts) < 3:
        return name
    region = parts[0].split("_")[-1]
    who = re.sub(r"_(fe)?male$", "", parts[1]).replace("_", " ")
    who = who.upper() if len(who) <= 3 else who.title()  # hfc -> HFC, nst -> NST, amy -> Amy
    return f"{who} ({gender(name)}, {region}, {parts[2]})"


def installed(voices_dir: str | Path, lang: str | None = None) -> list[str]:
    names = sorted(p.stem for p in Path(voices_dir).glob("*.onnx")
                   if p.with_suffix(".onnx.json").exists())
    if lang:
        names = [n for n in names if n.split("-")[0] in LANGUAGES[lang]]
    return names


def catalog(max_age_days: int = 7) -> dict:
    """The online voice catalog, cached for a week."""
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < max_age_days * 86400:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    with urllib.request.urlopen(CATALOG_URL, timeout=30) as resp:
        data = json.load(resp)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data), encoding="utf-8")
    return data


def available(lang: str) -> list[dict]:
    """Catalog voices for a language, female first, with size and install state."""
    out = []
    for name, v in catalog().items():
        if v["language"]["code"] in LANGUAGES[lang] and v.get("num_speakers", 1) == 1:
            size = sum(f["size_bytes"] for f in v["files"].values()) // 1_048_576
            out.append({"name": name, "label": label(name), "gender": gender(name), "size_mb": size})
    order = {"female": 0, "male": 1}
    return sorted(out, key=lambda v: (order.get(v["gender"], 2), v["name"]))


def download(name: str, voices_dir: str | Path) -> None:
    from piper.download_voices import download_voice
    Path(voices_dir).mkdir(parents=True, exist_ok=True)
    download_voice(name, Path(voices_dir))


def preview(name: str, voices_dir: str | Path, speed: float = 1.0, volume: float = 1.0,
            device: int | None = None) -> None:
    import numpy as np
    import sounddevice as sd
    from piper import PiperVoice, SynthesisConfig
    voice = PiperVoice.load(Path(voices_dir) / f"{name}.onnx")
    lang = "sv" if name.startswith("sv") else "en"
    cfg = SynthesisConfig(length_scale=1 / max(speed, 0.1), volume=volume)
    chunks = list(voice.synthesize(SAMPLE_TEXT[lang], syn_config=cfg))
    sd.play(np.concatenate([c.audio_int16_array for c in chunks]), chunks[0].sample_rate, device=device)
    sd.wait()
