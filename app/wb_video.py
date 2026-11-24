"""Moving scenes for movies: LTX-Video 2B 0.9.8 distilled (Lightricks) turns a painted scene into video.

Image-to-video: the scene's picture is the first frame and a prompt says what moves (people, water,
light, the camera). Clips are 768x448 at 24 fps, up to 10 s; a longer scene continues from the last
frame of the clip before. The distilled model needs only 8 denoising steps (about 35 s for 6-7 s of
video on an RTX 3080 Ti) and keeps characters steadier than the 0.9.5 model it replaced.

Files (install-video-maker.ps1, into AiProjects\\.hf): the transformer and VAE come from the single
checkpoint ltxv-2b-0.9.8-distilled.safetensors (6.3 GB, repo Lightricks/LTX-Video); the T5 text
encoder, tokenizer and scheduler from Lightricks/LTX-Video-0.9.5 (~19 GB). Everything must leave the
graphics card first (wb_create.make_room): with a song server or MusicGen still on it, the model spilled
into shared memory and one scene took over 20 minutes.
"""
from __future__ import annotations

import gc
import math
import time

import wb_create

BASE = "Lightricks/LTX-Video-0.9.5"                       # text encoder, tokenizer, scheduler
CKPT_REPO, CKPT = "Lightricks/LTX-Video", "ltxv-2b-0.9.8-distilled.safetensors"
MODEL = "LTX-Video 2B 0.9.8 distilled"
BASE_PATTERNS = ["text_encoder/*", "tokenizer/*", "scheduler/*", "model_index.json"]
CW, CH = 768, 448        # clip size: LTX needs multiples of 32; cropped to 16:9 when the movie is put together
FPS = 24
MAX_CLIP = 241           # frames per clip (10 s); longer scenes are chained clips
STRETCH = 1.2            # a clip may play up to 20 % slower to fill its scene
TIMESTEPS = [1000, 993, 987, 981, 975, 909, 725, 421]   # the distilled model's schedule
NEGATIVE = ("worst quality, inconsistent motion, blurry, jittery, distorted, deformed face, extra limbs, "
            "morphing, flicker, text, watermark")
_pipe = None


def installed() -> tuple[bool, str]:
    """Whether the model is in the local cache (never downloads from here)."""
    try:
        from huggingface_hub import hf_hub_download, snapshot_download
        snapshot_download(BASE, local_files_only=True, allow_patterns=BASE_PATTERNS)
        hf_hub_download(CKPT_REPO, CKPT, local_files_only=True)
        return True, MODEL
    except Exception:  # noqa: BLE001 - not downloaded (or hub missing): still pictures instead
        return False, "LTX-Video is not installed: run install-video-maker.ps1 in AiProjects"


def _load(emit):
    global _pipe
    if _pipe is None:
        import torch
        from diffusers import AutoencoderKLLTXVideo, LTXConditionPipeline, LTXVideoTransformer3DModel
        from diffusers.utils import logging as dlog
        from huggingface_hub import hf_hub_download
        dlog.set_verbosity_error()  # the distilled schedule is given as timesteps; diffusers warns on every clip
        emit("Loading LTX-Video…")
        ckpt = hf_hub_download(CKPT_REPO, CKPT, local_files_only=True)
        transformer = LTXVideoTransformer3DModel.from_single_file(ckpt, torch_dtype=torch.bfloat16)
        vae = AutoencoderKLLTXVideo.from_single_file(ckpt, torch_dtype=torch.bfloat16)
        pipe = LTXConditionPipeline.from_pretrained(BASE, transformer=transformer, vae=vae,
                                                    torch_dtype=torch.bfloat16, local_files_only=True)
        pipe.enable_model_cpu_offload()  # parts go to the graphics card only while they work
        pipe.vae.enable_tiling()         # decoding many frames at once would not fit in 12 GB otherwise
        pipe.set_progress_bar_config(disable=True)
        _pipe = pipe
    return _pipe


def unload() -> None:
    """Free the ~14 GB the model holds in memory once a movie is done."""
    global _pipe
    if _pipe is not None:
        _pipe = None
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except ImportError:
            pass


def _fit(img):
    """Crop and scale a picture to the clip size."""
    from PIL import Image
    img = img.convert("RGB")
    scale = max(CW / img.width, CH / img.height)
    img = img.resize((math.ceil(img.width * scale), math.ceil(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - CW) // 2, (img.height - CH) // 2
    return img.crop((left, top, left + CW, top + CH))


def _frames_for(seconds: float) -> int:
    """LTX wants 8k+1 frames."""
    n = max(49, min(MAX_CLIP, math.ceil(seconds * FPS)))
    return (n - 1 + 7) // 8 * 8 + 1


def animate(image, prompt: str, seconds: float, seed: int, emit=lambda m: None) -> list:
    """Frames (PIL, CWxCH) for a scene `seconds` long, starting from `image`."""
    import torch
    from diffusers.pipelines.ltx.pipeline_ltx_condition import LTXVideoCondition
    pipe = _load(emit)
    frames: list = []
    start = _fit(image)
    clip = 0
    # the movie spreads a scene's frames over its length, so up to STRETCH longer plays a little slower
    # (an 11 s scene from one 10 s clip runs at 22 fps) instead of costing another clip for the last second
    while len(frames) * STRETCH < seconds * FPS:
        clip += 1
        need = _frames_for(seconds - len(frames) / FPS)
        t = time.time()
        out = pipe(conditions=[LTXVideoCondition(image=start, frame_index=0)], prompt=prompt,
                   negative_prompt=NEGATIVE, width=CW, height=CH, num_frames=need, frame_rate=FPS,
                   guidance_scale=1.0, timesteps=TIMESTEPS, decode_timestep=0.05, decode_noise_scale=0.025,
                   image_cond_noise_scale=0.0, generator=torch.Generator("cuda").manual_seed(seed + clip)).frames[0]
        # the first frame of a follow-on clip repeats the last one before it
        frames += out if not frames else out[1:]
        start = out[-1]
        emit(f"…clip {clip}: {len(out)} frames in {time.time() - t:.0f} s")
    return frames[:max(1, round(seconds * FPS))]


def ready() -> tuple[bool, str]:
    ok, why = wb_create.cuda_ready()
    if not ok:
        return False, why
    return installed()
