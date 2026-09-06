"""One job per subprocess. Killing its process group also cancels FFmpeg/CUDA work."""
from __future__ import annotations
import json
import sys
from pathlib import Path
import numpy as np
from . import audio
from .store import track_by_id


def atomic_json(path: Path, value: dict):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def execute(context: dict, directory: Path) -> dict:
    request, project, files = context["request"], context["project"], context["files"]
    task = request["task"]
    result: dict = {"outputs": [], "patches": [], "metadata": {}}
    def progress(stage, value=None):
        atomic_json(directory / "progress.json", {"stage": stage, "progress": value})
    def add(path, name, role, **extra):
        result["outputs"].append({"path": str(path), "name": name, "role": role, **extra})
    track = track_by_id(project, request["track_id"]) if request.get("track_id") else None
    source = Path(files[track["asset_id"]]) if track else None
    progress("准备音频", 0.02)
    if task == "separate":
        from .models import separate
        original = next((t for t in project["tracks"] if t["role"] == "original"), None)
        if not original:
            raise ValueError("Import an original song first")
        voice, backing, provenance = separate(Path(files[original["source_asset_id"]]), directory, request["model"],
            Path(context["models"]), request["quality"], context["require_gpu"], progress)
        for path, name, role in [(backing, "伴奏", "accompaniment"), (voice, "原唱参考", "reference")]:
            current = path
            if project["key_shift"]:
                current = directory / ("keyed-" + path.name)
                audio.transpose(path, current, project["key_shift"])
            add(current, name, role, source_path=str(path), transpose=project["key_shift"], muted=role=="reference")
        result["mute_roles"] = ["original", "reference", "accompaniment"]
        result["metadata"]["provenance"] = provenance
    elif task == "transpose":
        targets = [t for t in project["tracks"] if t["role"] in {"original", "reference", "accompaniment"}]
        if not targets:
            raise ValueError("No backing track to transpose")
        for i, target in enumerate(targets):
            out = directory / f"transpose-{i}.wav"
            progress(f"高质量离线变调 {i+1}/{len(targets)}", i/len(targets))
            audio.transpose(Path(files[target["source_asset_id"]]), out, request["semitones"])
            add(out, target["name"], target["role"], replace=target["id"], transpose=request["semitones"])
        result["project_patch"] = {"key_shift": request["semitones"], "tonic": (project["tonic"]+request["semitones"]-project["key_shift"])%12}
    elif task in {"effects", "restore", "erase"}:
        out = directory / "processed.wav"
        if task == "effects":
            progress("降噪 → EQ → 压缩 → 齿音 → 空间效果", None)
            audio.effects(source, out, request["effects"], project["bpm"])
            add(out, track["name"]+" · 精修", "processed", offset=track["offset"], inherit=track["id"])
            result["mute_tracks"] = [track["id"]]
        elif task == "erase":
            # Selection coordinates are absolute timeline seconds.
            data = audio.erase(audio.load(source), request["start"]-track["offset"], request["end"]-track["offset"])
            audio.write(out, data)
            add(out, track["name"], track["role"], replace=track["id"])
        else:
            from .models import infer, find_stem
            outputs, provenance = infer(source, directory/"restoration", request["model"], Path(context["models"]),
                                         request["quality"], context["require_gpu"], progress)
            clean = find_stem(outputs, ["clean", "no noise", "no reverb", "dry", "vocals"])
            add(clean, track["name"]+" · AI 修复", "processed", offset=track["offset"], inherit=track["id"])
            result["mute_tracks"] = [track["id"]]
            result["metadata"]["provenance"] = [provenance]
    elif task in {"pitch", "tune", "harmony"}:
        from . import pitch
        analysis = pitch.analyze(source, request["pitch_engine"], progress)
        if task == "pitch":
            result["patches"] = [{"id": track["id"], "pitch": {**pitch.compact(analysis), "source_asset_id": track["asset_id"]}}]
        elif task == "tune":
            target = pitch.correction(analysis, request["tonic"], request["scale"], request["strength"], request["retune_ms"], request["notes"])
            progress("保留时序的 PSOLA 音准重合成", 0.88)
            out = directory / "tuned.wav"
            if request["strength"] == 0:
                import shutil
                shutil.copyfile(source, out)
            else:
                pitch.synthesize(source, out, analysis["times"], target)
            add(out, track["name"]+" · 调音", "processed", offset=track["offset"], inherit=track["id"],
                pitch={**pitch.compact(analysis, target), "source_asset_id": track["asset_id"]})
            result["mute_tracks"] = [track["id"]]
        else:
            for i, degree in enumerate(request["voices"]):
                progress(f"音阶和声 {i+1}/{len(request['voices'])} · 非生成式人声", None)
                target = pitch.harmony_target(analysis, degree, request["tonic"], request["scale"])
                out = directory / f"harmony-{i}.wav"
                pitch.synthesize(source, out, analysis["times"], target, harmony=True)
                add(out, f"和声 {degree:+d} 音阶级", "harmony", offset=track["offset"]+.012*(i+1), gain_db=-12,
                    pan=(-.65 if i%2==0 else .65), pitch={**pitch.compact(analysis, target), "source_asset_id": track["asset_id"]})
    elif task == "punch":
        progress("交叉淡化补录（原始 take 保留）", None)
        old = audio.load(source)
        start = request["start"] - track["offset"]
        end = request["end"] - track["offset"]
        out = directory / "comped.wav"
        audio.write(out, audio.punch(old, audio.load(Path(files[request["take_asset_id"]])), start, end))
        add(out, track["name"], track["role"], replace=track["id"])
    elif task == "automix":
        active = [t for t in project["tracks"] if not t["muted"] and (not any(x["solo"] for x in project["tracks"]) or t["solo"])]
        for i, t in enumerate(active):
            progress(f"测量响度 {i+1}/{len(active)}", i/max(1,len(active)))
            stats = audio.loudness(Path(files[t["asset_id"]]))
            measured = float(stats["input_i"])
            if np.isfinite(measured):
                target = -24 if t["role"] == "harmony" else -20 if t["role"] in {"accompaniment", "original"} else -17
                result["patches"].append({"id": t["id"], "gain_db": float(np.clip(target-measured, -24, 12))})
        result["metadata"]["note"] = "基于各轨综合响度的初始平衡；不会覆盖手工自动化，也不替代听感判断。"
    elif task == "export":
        progress("按轨道音量 / 声像 / 自动化渲染混音", .1)
        mix = directory / "premaster.wav"
        audio.mix(project, files, mix)
        progress("双遍 LUFS / True-Peak 母带处理", None)
        output = directory / f"simple-ktv-master.{request['format']}"
        report = audio.master(mix, output, request["target_lufs"], request["format"])
        result["export"] = str(output)
        result["metadata"]["mastering"] = report
        mix.unlink(missing_ok=True)
    else:
        raise ValueError("Unsupported task")
    progress("提交结果", .98)
    return result


def main():
    context_path = Path(sys.argv[1]).resolve()
    directory = context_path.parent
    context = json.loads(context_path.read_text(encoding="utf-8"))
    try:
        result = execute(context, directory)
        atomic_json(directory/"result.json", result)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        atomic_json(directory/"error.json", {"error": str(exc)[-1800:], "type": type(exc).__name__})
        raise SystemExit(1)


if __name__ == "__main__":
    main()
