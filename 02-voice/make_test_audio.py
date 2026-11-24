"""Make test questions as WAV files with Piper, so the pipeline can be tested without a microphone."""
import wave
from pathlib import Path

from piper import PiperVoice

HERE = Path(__file__).parent
QUESTIONS = {
    "q-en.wav": ("en_US-lessac-medium", "What is the tallest mountain in Sweden?"),
    "q-sv.wav": ("sv_SE-nst-medium", "Vad är huvudstaden i Norge?"),
    "q-tasks.wav": ("en_US-lessac-medium", "What is on my to do list?"),
}

if __name__ == "__main__":
    out_dir = HERE / "test-audio"
    out_dir.mkdir(exist_ok=True)
    for name, (voice_name, text) in QUESTIONS.items():
        voice = PiperVoice.load(HERE / "voices" / f"{voice_name}.onnx")
        with wave.open(str(out_dir / name), "wb") as w:
            voice.synthesize_wav(text, w)
        print("created", name, "-", text)
