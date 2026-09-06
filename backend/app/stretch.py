"""Native Rubber Band R3, two-pass offline pitch shifting via the documented C ABI.

No Python extension/compiler is needed. Docker installs librubberband2.
The library automatically compensates offline latency; we still enforce the exact
source frame count. See docs/RESEARCH.md for the upstream API references.
"""
from __future__ import annotations
import ctypes as C
from ctypes.util import find_library
from pathlib import Path
import numpy as np
import soundfile as sf


class RubberBandUnavailable(RuntimeError):
    pass


def library():
    name = find_library("rubberband")
    if not name:
        raise RubberBandUnavailable("Rubber Band R3 library missing. Use the Docker image (librubberband2).")
    lib = C.CDLL(name)
    state, uint, int_, floatp = C.c_void_p, C.c_uint, C.c_int, C.POINTER(C.c_float)
    floatpp = C.POINTER(floatp)
    signatures = {
        "rubberband_new": ([uint, uint, int_, C.c_double, C.c_double], state),
        "rubberband_delete": ([state], None),
        "rubberband_get_engine_version": ([state], int_),
        "rubberband_set_expected_input_duration": ([state,uint], None),
        "rubberband_set_max_process_size": ([state,uint], None),
        "rubberband_study": ([state,floatpp,uint,int_], None),
        "rubberband_process": ([state,floatpp,uint,int_], None),
        "rubberband_available": ([state], int_),
        "rubberband_retrieve": ([state,floatpp,uint], uint),
    }
    try:
        for name, (args, result) in signatures.items():
            function=getattr(lib,name); function.argtypes=args; function.restype=result
    except AttributeError as exc:
        raise RubberBandUnavailable("Rubber Band >=3 is required for the R3 engine.") from exc
    return lib


def pointers(planar: np.ndarray):
    pointer = C.POINTER(C.c_float)
    return (pointer * len(planar))(*(channel.ctypes.data_as(pointer) for channel in planar))


def shift(source: Path, output: Path, semitones: int):
    lib=library(); info=sf.info(source)
    if info.samplerate != 48000 or info.channels != 2:
        raise ValueError("Pitch shifting requires canonical 48 kHz stereo assets")
    # Offline | EngineFiner | ChannelsTogether | PitchHighQuality | FormantPreserved.
    options = 0x20000000 | 0x10000000 | 0x02000000 | 0x01000000
    state=lib.rubberband_new(info.samplerate,info.channels,options,1.,2**(semitones/12))
    if not state:
        raise RuntimeError("Could not allocate Rubber Band state")
    block=8192
    try:
        if lib.rubberband_get_engine_version(state) != 3:
            raise RubberBandUnavailable("Native library did not activate R3; refusing a silent quality downgrade")
        lib.rubberband_set_expected_input_duration(state,info.frames)
        lib.rubberband_set_max_process_size(state,block)
        # Study pass supplies the complete temporal structure before resynthesis.
        with sf.SoundFile(source) as inp:
            while inp.tell() < info.frames:
                data=np.ascontiguousarray(inp.read(block,dtype="float32",always_2d=True).T)
                lib.rubberband_study(state,pointers(data),data.shape[1],int(inp.tell()==info.frames))
        written=0
        with sf.SoundFile(source) as inp, sf.SoundFile(output,"w",samplerate=48000,channels=2,subtype="FLOAT") as out:
            def drain():
                nonlocal written
                while (available:=lib.rubberband_available(state)) > 0:
                    buf=np.empty((2,min(available,block)),dtype=np.float32)
                    count=lib.rubberband_retrieve(state,pointers(buf),buf.shape[1])
                    if not count:
                        raise RuntimeError("Rubber Band output stalled")
                    keep=min(count,max(0,info.frames-written))
                    if keep:
                        audio=buf[:,:keep].T
                        if not np.isfinite(audio).all():
                            raise RuntimeError("Pitch engine produced non-finite audio")
                        out.write(audio);written+=keep
            while inp.tell() < info.frames:
                data=np.ascontiguousarray(inp.read(block,dtype="float32",always_2d=True).T)
                lib.rubberband_process(state,pointers(data),data.shape[1],int(inp.tell()==info.frames))
                drain()
            drain()
            # Offline R3 should be exact. A rounding difference is bounded to 20 ms,
            # not concealed by padding a failed/truncated render with minutes of silence.
            missing=info.frames-written
            if missing > 960:
                raise RuntimeError(f"Pitch engine output was truncated by {missing} samples")
            if missing:
                out.write(np.zeros((missing,2),dtype=np.float32))
    finally:
        lib.rubberband_delete(state)
