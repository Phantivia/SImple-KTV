"""Deterministic audio I/O, editing, mixing and mastering. Audio remains on disk."""
from __future__ import annotations
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path
import numpy as np
import soundfile as sf
from .store import uid

SR = 48000


def command(args: list[str], timeout=600) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"{Path(args[0]).name} failed: {result.stderr[-1600:]}")
    return result


def ffmpeg(source: Path, output: Path, filters: str | None = None, *, codec="pcm_f32le", extra=None):
    args = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-protocol_whitelist", "file,pipe", "-i", str(source), "-vn"]
    if filters:
        args += ["-af", filters]
    args += ["-ar", str(SR), "-ac", "2", "-c:a", codec]
    args += extra or []
    args.append(str(output))
    command(args)


def probe(path: Path, max_duration=1200):
    try:
        p = command(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_format", "-show_streams", "-of", "json", str(path)], timeout=30)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Cannot read this audio file. It may be damaged or mislabeled.") from exc
    info = json.loads(p.stdout)
    streams = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        raise ValueError("No audio stream found")
    duration = float(info.get("format", {}).get("duration") or streams[0].get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0 or duration > max_duration:
        raise ValueError(f"Audio must be between 0 and {max_duration} seconds")
    return duration


def decode(source: Path, output: Path, max_duration=1200):
    probe(source, max_duration)
    # A hard decode limit also bounds misleading container duration metadata.
    args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file,pipe", "-i", str(source),
            "-t", str(max_duration + 0.05), "-map", "0:a:0", "-vn", "-ar", str(SR), "-ac", "2", "-c:a", "pcm_f32le", str(output)]
    command(args)
    info = sf.info(output)
    if info.duration > max_duration or info.frames == 0:
        output.unlink(missing_ok=True)
        raise ValueError("Decoded audio exceeds the duration limit")
    with sf.SoundFile(output) as f:
        for b in f.blocks(blocksize=65536, dtype="float32"):
            if not np.isfinite(b).all():
                raise ValueError("Audio contains NaN or infinite samples")


def metadata(path: Path, asset_id: str | None = None, bins=1600) -> dict:
    info = sf.info(path)
    count = min(bins, info.frames)
    step = max(1, math.ceil(info.frames / max(count, 1)))
    peaks = []
    peak = 0.0
    energy = 0.0
    with sf.SoundFile(path) as f:
        for block in f.blocks(blocksize=step, dtype="float32", always_2d=True):
            if not np.isfinite(block).all():
                raise ValueError("Non-finite audio samples")
            peak = max(peak, float(np.max(np.abs(block))))
            energy += float(np.sum(block.astype(np.float64) ** 2))
            # Max across channels avoids cancellation in out-of-phase stereo.
            peaks.append([round(float(np.min(block)), 5), round(float(np.max(block)), 5)])
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return {"id": asset_id or uid(), "filename": path.name, "duration": info.duration,
            "frames": info.frames, "sample_rate": info.samplerate, "channels": info.channels,
            "peaks": peaks, "peak_db": round(20 * math.log10(max(peak, 1e-9)), 2),
            "rms_db": round(10 * math.log10(max(energy / max(1, info.frames * info.channels), 1e-18)), 2),
            "sha256": h.hexdigest()}


def load(path: Path) -> np.ndarray:
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    if sr != SR:
        raise ValueError("Asset is not in the canonical 48 kHz format")
    return data


def write(path: Path, data: np.ndarray):
    if not np.isfinite(data).all():
        raise ValueError("Processing produced non-finite audio")
    sf.write(path, data, SR, subtype="FLOAT")


def erase(data: np.ndarray, start: float, end: float, fade_ms=8) -> np.ndarray:
    """Mute, never ripple-delete. Feather entirely INSIDE the selected interval."""
    result = data.copy()
    a, b = max(0, round(start * SR)), min(len(result), round(end * SR))
    if b <= a:
        raise ValueError("Selection does not overlap the track")
    n = min(round(fade_ms * SR / 1000), (b - a) // 2)
    result[a:b] = 0
    if n > 0:
        result[a:a+n] = data[a:a+n] * np.linspace(1, 0, n, dtype=np.float32)[:, None]
        result[b-n:b] = data[b-n:b] * np.linspace(0, 1, n, dtype=np.float32)[:, None]
    return result


def punch(base: np.ndarray, take: np.ndarray, start: float, end: float, fade_ms=12) -> np.ndarray:
    a, b = max(0, round(start * SR)), min(len(base), round(end * SR))
    if b <= a:
        raise ValueError("Punch range does not overlap the track")
    length = b - a
    # A truncated capture must not silently erase the unrecorded end of a phrase.
    if len(take) < length - int(0.03 * SR):
        raise ValueError("Take is shorter than the punch range; the original was not replaced")
    take = np.pad(take, ((0, max(0, length - len(take))), (0, 0)))[:length]
    output = base.copy()
    w = np.ones(length, dtype=np.float32)
    n = min(round(fade_ms * SR / 1000), length // 2)
    if n:
        w[:n] = np.linspace(0, 1, n)
        w[-n:] = np.linspace(1, 0, n)
    output[a:b] = base[a:b] * (1 - w[:, None]) + take * w[:, None]
    return output


def transpose(source: Path, output: Path, semitones: int):
    if semitones == 0:
        import shutil
        shutil.copyfile(source, output)
        return
    from .stretch import shift
    shift(source, output, semitones)


def effects(source: Path, output: Path, options: dict, bpm=100):
    filters = []
    if options["highpass"]:
        filters.append(f"highpass=f={options['highpass']}")
    if options["denoise"]:
        filters.append(f"afftdn=nr={3 + 15*options['denoise']}:nf=-40:tn=1")
    filters += [f"bass=g={options['low_db']}:f=160", f"equalizer=f=1600:t=q:w=0.8:g={options['mid_db']}", f"treble=g={options['high_db']}:f=6500"]
    if options["ratio"] > 1:
        filters.append(f"acompressor=threshold={10**(options['compressor_db']/20)}:ratio={options['ratio']}:attack=12:release=140:makeup=1")
    if options["deesser"]:
        filters.append(f"deesser=i={options['deesser']}:m=0.5:f=0.5")
    filters.append(f"volume={options['output_db']}dB")
    dry = output.with_name(output.stem + "-dry.wav")
    ffmpeg(source, dry, ",".join(filters))
    if options["reverb"] or options["delay"]:
        from pedalboard import Pedalboard, Reverb, Delay
        chain = Pedalboard([])
        if options["reverb"]:
            chain.append(Reverb(room_size=0.45, damping=0.6, wet_level=options["reverb"], dry_level=1.0, width=0.8))
        if options["delay"]:
            chain.append(Delay(delay_seconds=60 / bpm / 2, feedback=0.25, mix=options["delay"]))
        x = load(dry).T
        x = np.pad(x, ((0, 0), (0, SR * 2)))
        y = chain(x, SR).T
        # End an effect tail smoothly rather than on an arbitrary sample.
        y[-480:] *= np.linspace(1, 0, 480)[:, None]
        write(output, y)
        dry.unlink()
    else:
        dry.replace(output)


def stereo_pan(data: np.ndarray, pan: float) -> np.ndarray:
    """Matches Web Audio StereoPannerNode's stereo equal-power algorithm."""
    if pan == 0:
        return data
    out = np.empty_like(data)
    if pan <= 0:
        angle = (pan + 1) * math.pi / 2
        out[:, 0] = data[:, 0] + data[:, 1] * math.cos(angle)
        out[:, 1] = data[:, 1] * math.sin(angle)
    else:
        angle = pan * math.pi / 2
        out[:, 0] = data[:, 0] * math.cos(angle)
        out[:, 1] = data[:, 1] + data[:, 0] * math.sin(angle)
    return out


def automation_gain(track: dict, seconds: np.ndarray) -> np.ndarray:
    points = track.get("automation", [])
    db = np.interp(seconds, [p["time"] for p in points], [p["db"] for p in points]) if points else np.zeros_like(seconds)
    return np.power(10.0, (db + track["gain_db"]) / 20).astype(np.float32)


def mix(project: dict, files: dict[str, str], output: Path):
    tracks = [t for t in project["tracks"] if not t["muted"]]
    if any(t["solo"] for t in project["tracks"]):
        tracks = [t for t in tracks if t["solo"]]
    if not tracks:
        raise ValueError("No audible tracks. Unmute a track before exporting.")
    total = math.ceil(project["duration"] * SR)
    handles = [(t, sf.SoundFile(files[t["asset_id"]])) for t in tracks]
    try:
        with sf.SoundFile(output, "w", samplerate=SR, channels=2, subtype="FLOAT") as out:
            for pos in range(0, total, 65536):
                size = min(65536, total - pos)
                mixed = np.zeros((size, 2), dtype=np.float32)
                for track, f in handles:
                    offset = round(track["offset"] * SR)
                    left = max(pos, offset)
                    right = min(pos + size, offset + f.frames)
                    if right <= left:
                        continue
                    f.seek(left - offset)
                    data = f.read(right - left, dtype="float32", always_2d=True)
                    if data.shape[1] == 1:
                        data = np.repeat(data, 2, axis=1)
                    seconds = (np.arange(len(data), dtype=np.float64) + left - offset) / SR
                    data = stereo_pan(data, track["pan"]) * automation_gain(track, seconds)[:, None]
                    mixed[left-pos:right-pos] += data
                out.write(mixed)
    finally:
        for _, f in handles:
            f.close()


def loudness(source: Path, target=-14) -> dict:
    res = command(["ffmpeg", "-nostdin", "-hide_banner", "-i", str(source), "-af", f"loudnorm=I={target}:TP=-1:LRA=11:print_format=json", "-f", "null", "-"])
    matches = re.findall(r'\{\s*"input_i".*?\}', res.stderr, flags=re.S)
    if not matches:
        raise RuntimeError("FFmpeg did not produce a loudness report")
    return json.loads(matches[-1])


def master(source: Path, output: Path, target=-14, format="wav") -> dict:
    stats = loudness(source, target)
    fields = {"measured_I": "input_i", "measured_TP": "input_tp", "measured_LRA": "input_lra", "measured_thresh": "input_thresh", "offset": "target_offset"}
    finite = all(math.isfinite(float(stats[v])) for v in fields.values())
    filter_ = f"loudnorm=I={target}:TP=-1:LRA=11:linear=true:print_format=json" + "".join(f":{k}={stats[v]}" for k, v in fields.items()) if finite else "anull"
    codec = {"wav": "pcm_s24le", "mp3": "libmp3lame", "flac": "flac"}[format]
    args = ["ffmpeg", "-nostdin", "-hide_banner", "-y", "-i", str(source), "-af", filter_, "-ar", str(SR), "-ac", "2", "-c:a", codec]
    if format == "mp3":
        args += ["-b:a", "320k"]
    elif format == "flac":
        args += ["-sample_fmt", "s32"]
    args += [str(output)]
    report = command(args)
    out_stats = re.findall(r'\{\s*"input_i".*?\}', report.stderr, flags=re.S)
    return {"target_lufs": target, "true_peak_ceiling_db": -1, "silent": not finite,
            "measurement": json.loads(out_stats[-1]) if out_stats else stats,
            "note": "Lossy MP3 encoding can introduce intersample overshoots; use WAV/FLAC for the master."}


def parse_lrc(text: str) -> list[dict]:
    offset = re.search(r"\[offset:([+-]?\d+)\]", text)
    shift = int(offset.group(1)) / 1000 if offset else 0
    lines = []
    for line in text.splitlines():
        tags = list(re.finditer(r"\[(\d{1,3}):(\d{2}(?:\.\d{1,3})?)\]", line))
        lyric = re.sub(r"\[[^\]]*\]", "", line).strip()[:500]
        for tag in tags:
            time = int(tag.group(1)) * 60 + float(tag.group(2)) + shift
            if 0 <= time <= 2400:
                lines.append({"time": round(time, 3), "text": lyric})
    return sorted(lines, key=lambda x: x["time"])[:5000]


def demo(directory: Path) -> list[tuple[Path, str, str]]:
    """Original synthetic test fixture. No commercial music or singer recording."""
    rng = np.random.default_rng(7)
    duration = 24
    n = duration * SR
    t = np.arange(n, dtype=np.float64) / SR
    backing = np.zeros(n, dtype=np.float32)
    melody = np.zeros(n, dtype=np.float32)
    chords = [(48, 52, 55), (45, 48, 52), (53, 57, 60), (55, 59, 62)]
    notes = [64, 67, 69, 67, 64, 62, 60, 62, 65, 69, 72, 69, 67, 65, 62, 67]
    for i in range(16):
        a, b = i * SR * 3 // 2, (i + 1) * SR * 3 // 2
        local = t[a:b] - t[a]
        env = np.minimum(local / 0.02, 1) * np.exp(-local * 1.2) * np.minimum((1.5-local) / 0.03, 1)
        for midi in chords[(i // 4) % 4]:
            freq = 440 * 2 ** ((midi - 69) / 12)
            backing[a:b] += (0.09 * np.sin(2 * np.pi * freq * local) * env).astype(np.float32)
        freq = 440 * 2 ** ((notes[i] - 69 + 0.18) / 12)
        phase = 2 * np.pi * freq * local + 0.9 * np.sin(2*np.pi*5.3*local)
        voice = (np.sin(phase) + .35 * np.sin(2*phase) + .12 * np.sin(3*phase)) * .13
        melody[a:b] = (voice * np.minimum(local / .08, 1) * np.minimum((1.5-local) / .08, 1)).astype(np.float32)
    for i in range(48):
        a = i * SR // 2
        k = min(SR // 8, n - a)
        x = np.arange(k) / SR
        backing[a:a+k] += (0.12 * np.sin(2*np.pi*(65*x - 120*x*x)) * np.exp(-x*40)).astype(np.float32)
        if i % 2:
            backing[a:a+k] += (rng.standard_normal(k) * .025 * np.exp(-x*55)).astype(np.float32)
    result = []
    for filename, data, name, role in [("demo-original.wav", backing + melody, "PIXEL TIDE · 合成示例", "original"),
                                       ("demo-backing.wav", backing, "示例伴奏 · 非 AI 分离", "accompaniment"),
                                       ("demo-voice.wav", melody, "示例旋律 · 合成音色", "vocal")]:
        path = directory / filename
        write(path, np.column_stack([data, data]))
        result.append((path, name, role))
    return result
