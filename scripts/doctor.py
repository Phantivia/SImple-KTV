"""Run inside the image. Performs real native-library and (when required) CUDA checks."""
import importlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
import numpy as np
from app.audio import write
from app.stretch import library, shift


def main():
    report = {"ok": True, "checks": {}, "failures": []}
    def check(name, action):
        try:
            report["checks"][name] = action()
        except Exception as exc:
            report["ok"] = False
            report["failures"].append({"check": name, "error": str(exc)})
    def native():
        library()
        with tempfile.TemporaryDirectory() as temp:
            source, dest = Path(temp)/"source.wav", Path(temp)/"shifted.wav"
            x=.1*np.sin(2*np.pi*220*np.arange(48000)/48000)
            write(source,np.column_stack([x,x]))
            shift(source,dest,3)
            import soundfile as sf
            assert sf.info(dest).frames == len(x)
        return "R3 two-pass render executed; duration retained"
    check("rubberband",native)
    check("ffmpeg",lambda: subprocess.check_output(["ffmpeg","-version"],text=True).splitlines()[0])
    for name in ["fastapi","soundfile","librosa","parselmouth","pedalboard"]:
        check(name,lambda name=name: getattr(importlib.import_module(name),"__version__","import OK"))
    if os.getenv("KTV_REQUIRE_GPU") == "1":
        def cuda():
            import torch
            assert torch.cuda.is_available(), "CUDA is not visible inside this container"
            # Real kernels, including matrix multiplication; not just a device-name check.
            x=torch.randn(128,128,device="cuda")
            y=x@x
            assert torch.isfinite(y).all().item()
            torch.cuda.synchronize()
            props=torch.cuda.get_device_properties(0)
            return {"torch":torch.__version__,"runtime":torch.version.cuda,"device":props.name,
                    "memory_gib":round(props.total_memory/1024**3,2),"capability":f"{props.major}.{props.minor}"}
        check("cuda",cuda)
        check("separator",lambda: str(importlib.import_module("audio_separator.separator").Separator))
        check("torchcrepe",lambda: str(importlib.import_module("torchcrepe").predict))
    else:
        report["checks"]["ai"]="CPU image: RoFormer / neural F0 unavailable by design"
    print(json.dumps(report,indent=2,ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__=="__main__":
    raise SystemExit(main())
