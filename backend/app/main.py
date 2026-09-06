from __future__ import annotations
import asyncio
import importlib.util
import json
import os
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, File, UploadFile, Query, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from .config import Settings, settings
from .schemas import TrackPatch, ProjectPatch, Revision, JobRequest
from .store import Store, Conflict, Missing, new_track, track_by_id, uid
from .jobs import Jobs
from .models import MODELS
from . import audio


def create_app(config: Settings = settings) -> FastAPI:
    store = Store(config.data)
    config.models.mkdir(parents=True, exist_ok=True)
    manager = Jobs(store, config)
    health_cache = {"time": 0.0, "value": {}}

    @asynccontextmanager
    async def lifespan(app):
        yield
        await asyncio.to_thread(manager.close)

    app = FastAPI(title="Simple KTV", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
    app.state.store, app.state.jobs = store, manager

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.url.hostname or ""
        allowed = {"localhost", "127.0.0.1", "::1", "testserver"} | set(os.getenv("KTV_ALLOWED_HOSTS", "").split(","))
        if host not in allowed:
            return JSONResponse({"detail": "Untrusted Host header. This service is localhost-only by default."}, status_code=400)
        if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.url.netloc}"
            if origin and origin != expected:
                return JSONResponse({"detail": "Cross-origin writes are not allowed"}, status_code=403)
            if request.headers.get("x-ktv-client") != "1":
                return JSONResponse({"detail": "X-KTV-Client: 1 is required for write requests"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "microphone=(self), camera=()"
        if request.url.path != "/api/docs":
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api") else "no-cache"
        return response

    @app.exception_handler(Conflict)
    async def conflict(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(Missing)
    async def missing(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    def hydrate(project_id):
        p = store.get(project_id)
        asset_ids = {t["asset_id"] for t in p["tracks"]}
        p["assets"] = {a: store.asset(project_id, a) for a in asset_ids}
        return p

    def capabilities():
        if time.monotonic()-health_cache["time"] < 30:
            return health_cache["value"]
        value = {"ok": True, "sample_rate": audio.SR, "version": "0.1.0", "device": "cpu", "gpu": None,
                 "max_upload_mb": config.max_upload//1024//1024, "max_duration": config.max_duration,
                 "separation": importlib.util.find_spec("audio_separator") is not None,
                 "torchcrepe": importlib.util.find_spec("torchcrepe") is not None,
                 "ffmpeg": bool(shutil.which("ffmpeg")), "gpu_required": config.require_gpu, "gpu_error": None}
        try:
            import torch
            value["torch"] = torch.__version__
            value["cuda_runtime"] = torch.version.cuda
            if torch.cuda.is_available():
                torch.zeros(1, device="cuda").add_(1).item()
                props = torch.cuda.get_device_properties(0)
                value.update(device="cuda", gpu={"name": props.name, "vram_gb": round(props.total_memory/1024**3, 1),
                    "compute_capability": f"{props.major}.{props.minor}"})
        except ImportError:
            value["torch"] = None
        except Exception as exc:
            value["gpu_error"] = str(exc)[-500:]
        health_cache.update(time=time.monotonic(), value=value)
        return value

    @app.get("/api/health")
    async def health():
        return await asyncio.to_thread(capabilities)

    @app.get("/api/models")
    def models():
        return [{"id": key, **value, "downloaded": (config.models/value["filename"]).is_file(),
                 "license_note": "代码许可与模型权重许可分开；未代替你确认商业使用授权。"} for key,value in MODELS.items()]

    @app.get("/api/projects")
    def projects():
        return store.list()

    async def receive(upload: UploadFile) -> Path:
        ext = Path(upload.filename or "upload.wav").suffix.lower()
        if ext not in {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".webm"}:
            raise ValueError("Unsupported audio format. Use MP3, WAV, FLAC, M4A, AAC, OGG, Opus or WebM.")
        directory = config.data/"incoming"
        directory.mkdir(exist_ok=True)
        path = directory/(uid()+ext)
        size = 0
        try:
            with path.open("wb") as f:
                while chunk := await upload.read(1024*1024):
                    size += len(chunk)
                    if size > config.max_upload:
                        raise ValueError(f"Upload exceeds {config.max_upload//1024//1024} MB")
                    f.write(chunk)
            if not size:
                raise ValueError("Uploaded file is empty")
            return path
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()

    def register_decoded(project_id: str, source: Path) -> dict:
        asset_id = uid()
        dest = store.project_dir(project_id)/"assets"/(asset_id+".wav")
        shutil.copyfile(source, dest)
        info = audio.metadata(dest, asset_id)
        store.add_asset(project_id, info)
        return info

    @app.post("/api/projects/import", status_code=201)
    async def import_song(file: UploadFile = File(...)):
        filename = file.filename or "Untitled"
        source = await receive(file)
        decoded = source.with_name(source.stem+"-decoded.wav")
        try:
            def ingest():
                audio.decode(source, decoded, config.max_duration)
                p = store.create(Path(filename).stem)
                asset = register_decoded(p["id"], decoded)
                store.mutate(p["id"], p["revision"], lambda p: p["tracks"].append(new_track(asset, "原版歌曲", "original")))
                return hydrate(p["id"])
            return await asyncio.to_thread(ingest)
        finally:
            source.unlink(missing_ok=True)
            decoded.unlink(missing_ok=True)

    @app.post("/api/projects/demo", status_code=201)
    async def create_demo():
        def make():
            p = store.create("PIXEL TIDE · 合成演示")
            with tempfile.TemporaryDirectory(dir=config.data) as tmp:
                tracks = []
                for path,name,role in audio.demo(Path(tmp)):
                    asset = register_decoded(p["id"], path)
                    tracks.append(new_track(asset, name, role, muted=role=="original", gain_db=-3))
                def change(p):
                    p["tracks"] = tracks
                    p["bpm"] = 120
                    p["lyrics"] = [{"time": 0, "text": "此刻，让声音发生。"}, {"time": 6, "text": "每一个像素，都有你的频率。"},
                                   {"time": 12, "text": "YOUR VOICE. IN EVERY PIXEL."}, {"time": 18, "text": "合成旋律演示 · 不含商业歌曲"}]
                store.mutate(p["id"], p["revision"], change)
            return hydrate(p["id"])
        return await asyncio.to_thread(make)

    @app.get("/api/projects/{project_id}")
    def project(project_id: str):
        return hydrate(project_id)

    @app.patch("/api/projects/{project_id}")
    def patch_project(project_id: str, patch: ProjectPatch):
        fields = patch.model_dump(exclude_none=True, exclude={"revision"})
        if "lyrics" in fields:
            fields["lyrics"] = audio.parse_lrc(fields["lyrics"])
        store.mutate(project_id, patch.revision, lambda p: p.update(fields))
        return hydrate(project_id)

    @app.patch("/api/projects/{project_id}/tracks/{track_id}")
    def patch_track(project_id: str, track_id: str, patch: TrackPatch):
        def change(p):
            t = track_by_id(p, track_id)
            t.update(patch.model_dump(exclude_none=True, exclude={"revision"}))
            if t["offset"] + t["duration"] > config.max_duration+10:
                raise ValueError("Track extends beyond the project duration limit")
        store.mutate(project_id, patch.revision, change)
        return hydrate(project_id)

    @app.delete("/api/projects/{project_id}/tracks/{track_id}")
    def delete_track(project_id: str, track_id: str, revision: int = Query(ge=1)):
        def change(p):
            track_by_id(p, track_id)
            p["tracks"] = [t for t in p["tracks"] if t["id"] != track_id]
        store.mutate(project_id, revision, change)
        return hydrate(project_id)

    @app.post("/api/projects/{project_id}/undo")
    def undo(project_id: str, request: Revision):
        store.travel(project_id, request.revision, -1)
        return hydrate(project_id)

    @app.post("/api/projects/{project_id}/redo")
    def redo(project_id: str, request: Revision):
        store.travel(project_id, request.revision, 1)
        return hydrate(project_id)

    @app.get("/api/projects/{project_id}/assets/{asset_id}")
    def asset(project_id: str, asset_id: str, download: bool = False):
        path = store.asset_path(project_id, asset_id)
        return FileResponse(path, media_type="audio/wav", filename=path.name if download else None)

    @app.post("/api/projects/{project_id}/recordings", status_code=201)
    async def recording(project_id: str, file: UploadFile = File(...), revision: int = Query(ge=1),
                        offset: float = Query(default=0, ge=0, le=2400), target: str | None = None,
                        end: float | None = Query(default=None, gt=0, le=2400)):
        p = store.get(project_id)
        if p["revision"] != revision:
            raise Conflict("Project changed before recording upload")
        if target:
            track_by_id(p, target)
            if end is None or end <= offset:
                raise ValueError("Punch end must be after start")
        source = await receive(file)
        decoded = source.with_name(source.stem+"-decoded.wav")
        try:
            def ingest():
                audio.decode(source, decoded, config.max_duration)
                asset = register_decoded(project_id, decoded)
                if offset+asset["duration"] > config.max_duration+10:
                    raise ValueError("Recording exceeds the project duration limit")
                t = new_track(asset, f"Take {sum(x['role']=='vocal' for x in p['tracks'])+1:02d}", "vocal", offset=offset, muted=bool(target))
                store.mutate(project_id, revision, lambda p: p["tracks"].append(t))
                job = None
                if target:
                    job = manager.submit(project_id, {"revision": revision+1, "task": "punch", "track_id": target,
                        "take_asset_id": asset["id"], "start": offset, "end": end})
                return {"project": hydrate(project_id), "job": job, "track_id": t["id"]}
            return await asyncio.to_thread(ingest)
        finally:
            source.unlink(missing_ok=True)
            decoded.unlink(missing_ok=True)

    @app.post("/api/projects/{project_id}/jobs", status_code=202)
    def submit(project_id: str, request: JobRequest):
        caps = capabilities()
        if request.task in {"separate", "restore"} and not caps["separation"]:
            return JSONResponse({"detail": "当前是基础 CPU 环境，未安装 AI 分离模型。请使用 GPU Docker 启动脚本。"}, status_code=503)
        if request.pitch_engine == "torchcrepe" and request.task in {"pitch", "tune", "harmony"} and not caps["torchcrepe"]:
            return JSONResponse({"detail": "TorchCREPE 未安装。使用 GPU 镜像，或显式选择 pYIN 算法。"}, status_code=503)
        if config.require_gpu and request.task in {"separate", "restore"} and caps["device"] != "cuda":
            return JSONResponse({"detail": "GPU profile requires CUDA, but the kernel probe failed. See device diagnostics."}, status_code=503)
        return manager.submit(project_id, request.model_dump())

    @app.get("/api/projects/{project_id}/jobs")
    def project_jobs(project_id: str):
        store.get(project_id)
        return [{k:v for k,v in j.items() if k != "output"} for j in store.jobs(project_id)]

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return {k:v for k,v in store.job(job_id).items() if k != "output"}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str):
        result = await asyncio.to_thread(manager.cancel, job_id)
        return {k:v for k,v in result.items() if k != "output"}

    @app.get("/api/jobs/{job_id}/download")
    def download(job_id: str):
        j = store.job(job_id)
        if j["state"] != "completed" or not j.get("output"):
            raise Missing("Completed export not found")
        path = Path(j["output"]).resolve()
        directory = store.project_dir(j["project"])/"jobs"/job_id
        if not path.is_relative_to(directory.resolve()) or not path.is_file():
            raise Missing("Export file not found")
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")

    @app.get("/api/jobs/{job_id}/log")
    def log(job_id: str):
        j = store.job(job_id)
        path = store.project_dir(j["project"])/"jobs"/job_id/"worker.log"
        if not path.is_file():
            raise Missing("No worker log yet")
        return FileResponse(path, media_type="text/plain", filename=f"ktv-job-{job_id}.log")

    @app.get("/api/projects/{project_id}/manifest")
    def manifest(project_id: str):
        return JSONResponse(hydrate(project_id), headers={"Content-Disposition": 'attachment; filename="simple-ktv-project.json"'})

    if config.static.is_dir() and (config.static/"index.html").exists():
        app.mount("/", StaticFiles(directory=config.static, html=True), name="frontend")
    else:
        @app.get("/")
        def no_build():
            return JSONResponse({"detail": "Frontend not built. Run npm run build in frontend, or use Docker."}, status_code=503)
    return app


app = create_app()
