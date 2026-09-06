"""Monophonic pitch analysis + editable, conservative PSOLA resynthesis.

TorchCREPE is an actual neural F0 estimator; pYIN is explicitly the CPU DSP path.
Harmony here is scale-aware DSP, NOT a generative singer/voice-cloning model.
"""
from pathlib import Path
from typing import Callable
import numpy as np
from scipy.ndimage import gaussian_filter1d, median_filter
from .audio import SR, load, write

SCALES = {"major": [0, 2, 4, 5, 7, 9, 11], "minor": [0, 2, 3, 5, 7, 8, 10], "chromatic": list(range(12))}


def scale_notes(tonic: int, scale: str) -> np.ndarray:
    return np.array([m for m in range(12, 121) if (m - tonic) % 12 in SCALES[scale]], dtype=np.float64)


def nearest(notes: np.ndarray, tonic: int, scale: str) -> np.ndarray:
    allowed = scale_notes(tonic, scale)
    return allowed[np.argmin(np.abs(notes[:, None] - allowed[None, :]), axis=1)]


def analyze(source: Path, engine="pyin", progress: Callable | None = None) -> dict:
    import librosa
    x = load(source).mean(axis=1)
    mono = librosa.resample(x, orig_sr=SR, target_sr=16000)
    hop = 160
    size = 16000 * 20
    # Independent, overlapping chunks bound neural and pYIN memory consumption.
    overlap = 1600
    all_times, all_f0, all_conf = [], [], []
    for a in range(0, len(mono), size):
        left, right = max(0, a-overlap), min(len(mono), a+size+overlap)
        segment = mono[left:right]
        if engine == "torchcrepe":
            import torch
            import torchcrepe
            device = "cuda" if torch.cuda.is_available() else "cpu"
            with torch.inference_mode():
                f0, confidence = torchcrepe.predict(torch.from_numpy(segment.copy()).unsqueeze(0).to(device),
                    16000, hop, 65, 1000, model="full", batch_size=256, device=device, return_periodicity=True)
            f0 = f0.squeeze(0).cpu().numpy()
            conf = confidence.squeeze(0).cpu().numpy()
            f0 = median_filter(f0, size=3)
        else:
            f0, _, conf = librosa.pyin(segment, sr=16000, fmin=65, fmax=1000, frame_length=1024, hop_length=hop)
            conf = np.nan_to_num(conf)
        times = left/16000 + np.arange(len(f0)) * .01
        power = librosa.feature.rms(y=segment, frame_length=1024, hop_length=hop)[0]
        valid_power = power[:len(f0)] > .002
        if len(valid_power) < len(f0):
            valid_power = np.pad(valid_power, (0, len(f0)-len(valid_power)))
        f0 = np.where((conf >= (0.6 if engine == "torchcrepe" else 0.1)) & valid_power, f0, np.nan)
        keep = (times >= a/16000) & (times < min(a+size, len(mono))/16000)
        all_times.extend(times[keep])
        all_f0.extend(f0[keep])
        all_conf.extend(conf[keep])
        if progress:
            progress(f"音高分析 {min(a+size, len(mono))/16000:.0f} / {len(mono)/16000:.0f} 秒", min(0.85, (a+size)/max(1,len(mono))*.85))
    times = np.array(all_times)
    f0 = np.array(all_f0)
    voiced = np.isfinite(f0) & (f0 > 0)
    midi = np.full_like(f0, np.nan)
    midi[voiced] = 69 + 12*np.log2(f0[voiced]/440)
    return {"times": times, "f0": f0, "midi": midi, "confidence": np.array(all_conf), "engine": engine}


def note_regions(analysis: dict) -> list[dict]:
    times, midi = analysis["times"], analysis["midi"]
    notes = median_filter(np.where(np.isfinite(midi), np.rint(midi), -1), size=5)
    result = []
    start = 0
    for i in range(1, len(notes)+1):
        if i == len(notes) or notes[i] != notes[start]:
            if notes[start] > 0 and (i-start)*.01 >= .08:
                result.append({"start": round(float(times[start]), 3), "end": round(float(times[i-1])+.01, 3), "midi": float(notes[start])})
            start = i
    return result


def compact(analysis: dict, target: np.ndarray | None = None) -> dict:
    stride = max(1, len(analysis["times"]) // 5000)
    def clean(values):
        return [round(float(v), 3) if np.isfinite(v) else None for v in values[::stride]]
    return {"engine": analysis["engine"], "times": clean(analysis["times"]), "midi": clean(analysis["midi"]),
            "target": clean(target) if target is not None else None, "notes": note_regions(analysis),
            "monophonic_only": True}


def correction(analysis: dict, tonic=0, scale="major", strength=.75, retune_ms=100, manual=None):
    midi = analysis["midi"].copy()
    valid = np.isfinite(midi)
    target = midi.copy()
    if strength == 0:
        return midi
    if not valid.any():
        raise ValueError("No stable voiced notes found. Use a dry, isolated monophonic vocal.")
    target[valid] = nearest(midi[valid], tonic, scale)
    for note in manual or []:
        mask = valid & (analysis["times"] >= note["start"]) & (analysis["times"] < note["end"])
        target[mask] = note["midi"]
    delta = np.where(valid, target-midi, 0) * strength
    if retune_ms > 0:
        # Smooth only the correction, not the original expressive F0 contour.
        delta = gaussian_filter1d(delta, max(.1, retune_ms/20), mode="nearest")
    return np.where(valid, midi + delta, np.nan)


def synthesize(source: Path, output: Path, times: np.ndarray, target: np.ndarray, *, harmony=False):
    import parselmouth
    from parselmouth.praat import call
    x = load(source).mean(axis=1)
    sound = parselmouth.Sound(x.astype(np.float64), sampling_frequency=SR)
    manipulation = call(sound, "To Manipulation", .01, 60, 1100)
    tier = call(manipulation, "Extract pitch tier")
    call(tier, "Remove points between", 0, len(x)/SR)
    count = 0
    for time, midi in zip(times, target):
        if np.isfinite(midi) and 0 <= time <= len(x)/SR:
            call(tier, "Add point", float(time), float(np.clip(440 * 2**((midi-69)/12), 40, 1800)))
            count += 1
    if count < 2:
        raise ValueError("Not enough voiced audio to synthesize")
    call([tier, manipulation], "Replace pitch tier")
    result = call(manipulation, "Get resynthesis (overlap-add)").values[0].astype(np.float32)
    result = np.pad(result, (0, max(0, len(x)-len(result))))[:len(x)]
    if harmony:
        # Attenuate duplicated consonants/noise in harmony layers.
        envelope = gaussian_filter1d(np.isfinite(target).astype(float), sigma=2)
        result *= np.interp(np.arange(len(result))/SR, times, envelope*.85+.15).astype(np.float32)
    write(output, np.column_stack([result, result]))


def harmony_target(analysis: dict, degree=2, tonic=0, scale="major"):
    midi = analysis["midi"]
    valid = np.isfinite(midi)
    allowed = scale_notes(tonic, scale)
    result = midi.copy()
    indexes = np.argmin(np.abs(midi[valid, None]-allowed), axis=1)
    shifted = np.clip(indexes+degree, 0, len(allowed)-1)
    result[valid] = allowed[shifted] + (midi[valid]-allowed[indexes])*.65
    return result
