"""Allowlisted upstream model adapters, isolated in the job subprocess."""
from __future__ import annotations
import gc
import hashlib
import logging
from pathlib import Path
import numpy as np
from . import audio

MODELS = {
    "bs-roformer": {"name": "BS-RoFormer · ViperX 1297", "filename": "model_bs_roformer_ep_317_sdr_12.9755.ckpt", "task": "separate"},
    "melband": {"name": "MelBand RoFormer · Kim", "filename": "vocals_mel_band_roformer.ckpt", "task": "separate"},
    "denoise": {"name": "MelBand RoFormer · Denoise aufr33", "filename": "denoise_mel_band_roformer_aufr33_sdr_27.9959.ckpt", "task": "restore"},
    "dereverb": {"name": "MelBand RoFormer · DeReverb anvuew", "filename": "dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt", "task": "restore"},
}


def infer(source: Path, directory: Path, model_id: str, models: Path, quality: str, require_gpu: bool, progress):
    try:
        import torch
        from audio_separator.separator import Separator
    except ImportError as exc:
        raise RuntimeError("AI packages are not installed in this image. Start the GPU profile to use RoFormer.") from exc
    if require_gpu and not torch.cuda.is_available():
        raise RuntimeError("CUDA unavailable. Check Docker GPU passthrough, the NVIDIA driver, and the CUDA 12.8 PyTorch image.")
    if torch.cuda.is_available():
        # Execute a real kernel; is_available alone does not prove sm_120 compatibility.
        torch.zeros(1, device="cuda").add_(1).item()
    directory.mkdir(parents=True, exist_ok=True)
    model = MODELS[model_id]
    progress("载入模型 / 首次使用将从上游下载权重", None)
    separator = Separator(model_file_dir=str(models), output_dir=str(directory), output_format="WAV",
        normalization_threshold=1.0, amplification_threshold=0.0, sample_rate=44100,
        use_soundfile=True, use_autocast=torch.cuda.is_available(), log_level=logging.INFO,
        mdxc_params={"segment_size": 256, "override_model_segment_size": False,
                     "batch_size": 1, "overlap": 8 if quality == "quality" else 4, "pitch_shift": 0})
    # No arbitrary paths, URLs, uploaded pickle checkpoints, or remote inference.
    separator.load_model(model_filename=model["filename"])
    progress(f"{model['name']} · 推理中（上游未提供逐块进度）", None)
    names = {"Vocals": "vocals", "Instrumental": "instrumental", "Other": "instrumental",
             "No Noise": "clean", "No Reverb": "clean", "Dry": "clean", "Noise": "noise", "Reverb": "reverb"}
    files = separator.separate(str(source), custom_output_names=names)
    normalized = {}
    for name in files:
        path = Path(name)
        if not path.is_absolute():
            path = directory / path
        if not path.resolve().is_relative_to(directory.resolve()):
            raise RuntimeError("Separator returned an unexpected output path")
        dest = directory / ("canonical-" + path.stem + ".wav")
        audio.ffmpeg(path, dest)
        normalized[path.stem.lower()] = dest
    del separator
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    weight = models / model["filename"]
    digest = None
    if weight.is_file():
        h = hashlib.sha256()
        with weight.open("rb") as f:
            for block in iter(lambda: f.read(1024*1024), b""):
                h.update(block)
        digest = h.hexdigest()
    return normalized, {"model": model_id, "filename": model["filename"], "sha256": digest,
                        "device": "cuda" if torch.cuda.is_available() else "cpu", "quality": quality}


def find_stem(outputs: dict[str, Path], words: list[str]) -> Path:
    for word in words:
        for name, path in outputs.items():
            if name == word or name.endswith("_"+word) or name.startswith(word):
                return path
    raise RuntimeError(f"Model did not produce the expected stem ({', '.join(words)}). Outputs: {list(outputs)}")


def separate(source: Path, directory: Path, model_id: str, models: Path, quality: str, require_gpu: bool, progress):
    members = ["bs-roformer", "melband"] if model_id == "ensemble" else [model_id]
    vocals, provenance = [], []
    for i, member in enumerate(members):
        results, info = infer(source, directory / f"member-{i}", member, models, quality, require_gpu, progress)
        vocals.append(audio.load(find_stem(results, ["vocals"])))
        provenance.append(info)
    mixture = audio.load(source)
    # A single residual definition ensures mixture == vocals + backing exactly.
    # Sequential ensemble is a choice to audition, never an asserted universal improvement.
    average = np.zeros_like(mixture)
    for stem in vocals:
        length = min(len(stem), len(average))
        average[:length] += stem[:length] / len(vocals)
    voice = directory / "vocals.wav"
    backing = directory / "instrumental.wav"
    audio.write(voice, average)
    audio.write(backing, mixture-average)
    return voice, backing, provenance
