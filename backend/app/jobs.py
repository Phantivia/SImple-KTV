from __future__ import annotations
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .store import Store, Conflict, uid, now, new_track, track_by_id
from .audio import metadata
from .config import Settings

ACTIVE = {"queued", "running", "cancelling"}


class Jobs:
    def __init__(self, store: Store, config: Settings):
        self.store, self.config = store, config
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ktv-job")
        self.lock = threading.RLock()
        self.processes: dict[str, subprocess.Popen] = {}
        self.closed = False
        for doc in store.jobs():
            if doc["state"] in ACTIVE:
                doc.update(state="failed", error="Server restarted while this job was active; retry explicitly.", finished_at=now())
                store.save_job(doc)

    def submit(self, project_id: str, request: dict) -> dict:
        with self.lock, self.store.lock:
            if self.closed:
                raise Conflict("Server is shutting down")
            project = self.store.get(project_id)
            if project["revision"] != request["revision"]:
                raise Conflict("Project changed. Reload and retry.")
            if any(j["state"] in ACTIVE for j in self.store.jobs(project_id)):
                raise Conflict("This project already has a queued or running job")
            if sum(j["state"] in ACTIVE for j in self.store.jobs()) >= 8:
                raise Conflict("The local processing queue is full")
            if request.get("track_id"):
                track_by_id(project, request["track_id"])
            ids = {a for t in project["tracks"] for a in (t["asset_id"], t["source_asset_id"])}
            if request.get("take_asset_id"):
                ids.add(request["take_asset_id"])
            files = {i: str(self.store.asset_path(project_id, i)) for i in ids}
            job_id = uid()
            directory = self.store.project_dir(project_id)/"jobs"/job_id
            directory.mkdir(parents=True)
            context = {"request": request, "project": project, "files": files, "models": str(self.config.models), "require_gpu": self.config.require_gpu}
            (directory/"request.json").write_text(json.dumps(context), encoding="utf-8")
            job = {"id": job_id, "project": project_id, "task": request["task"], "state": "queued", "progress": None,
                   "stage": "已排队", "created_at": now(), "error": None, "result": None}
            self.store.save_job(job)
            self.pool.submit(self._run, job_id, directory, request["revision"])
            return job

    @staticmethod
    def kill(process):
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=5)
        except ProcessLookupError:
            pass

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            doc = self.store.job(job_id)
            if doc["state"] not in ACTIVE:
                return doc
            doc["state"] = "cancelling" if job_id in self.processes else "cancelled"
            doc["stage"] = "正在取消" if doc["state"] == "cancelling" else "已取消"
            if doc["state"] == "cancelled":
                doc["finished_at"] = now()
            self.store.save_job(doc)
            process = self.processes.get(job_id)
        if process:
            self.kill(process)
        return self.store.job(job_id)

    def _run(self, job_id: str, directory: Path, revision: int):
        try:
            with self.lock:
                job = self.store.job(job_id)
                if job["state"] != "queued":
                    return
                job.update(state="running", started_at=now(), stage="启动隔离任务进程")
                self.store.save_job(job)
                env = {**os.environ, "PYTHONUNBUFFERED": "1", "OMP_NUM_THREADS": "4", "NUMBA_NUM_THREADS": "4", "OPENBLAS_NUM_THREADS": "4"}
                with (directory/"worker.log").open("w", encoding="utf-8") as log:
                    process = subprocess.Popen([sys.executable, "-m", "app.worker", str(directory/"request.json")],
                        cwd=Path(__file__).parents[1], stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=os.name=="posix")
                self.processes[job_id] = process
            started = time.monotonic()
            while process.poll() is None:
                if time.monotonic()-started > self.config.job_timeout:
                    self.kill(process)
                    raise TimeoutError("Job timed out. Try a shorter recording or the balanced profile.")
                path = directory/"progress.json"
                if path.exists():
                    try:
                        status = json.loads(path.read_text(encoding="utf-8"))
                        with self.lock:
                            job = self.store.job(job_id)
                            if job["state"] == "running":
                                job.update(status)
                                self.store.save_job(job)
                    except (OSError, json.JSONDecodeError):
                        pass
                time.sleep(.25)
            with self.lock:
                job = self.store.job(job_id)
                if job["state"] == "cancelling":
                    job.update(state="cancelled", stage="已取消；原音频未改变", finished_at=now())
                    self.store.save_job(job)
                    return
                if process.returncode:
                    error_path = directory/"error.json"
                    error = json.loads(error_path.read_text(encoding="utf-8"))["error"] if error_path.exists() else f"Worker exited with code {process.returncode}; inspect the job log."
                    raise RuntimeError(error)
                result = json.loads((directory/"result.json").read_text(encoding="utf-8"))
                public = self._commit(job["project"], revision, result, directory)
                job.update(state="completed", stage="已完成", progress=1, result=public, finished_at=now())
                if result.get("export"):
                    job["output"] = result["export"]
                self.store.save_job(job)
        except Exception as exc:
            with self.lock:
                job = self.store.job(job_id)
                job.update(state="failed", error=str(exc)[-1800:], stage="任务失败；原音频保留", finished_at=now())
                self.store.save_job(job)
        finally:
            with self.lock:
                self.processes.pop(job_id, None)

    def _commit(self, project_id: str, revision: int, result: dict, directory: Path) -> dict:
        assets = []
        def register(path_: str) -> dict:
            path = Path(path_).resolve()
            if not path.is_relative_to(directory.resolve()) or not path.is_file():
                raise ValueError("Worker output is outside its staging directory")
            asset_id = uid()
            dest = self.store.project_dir(project_id)/"assets"/(asset_id+".wav")
            shutil.copyfile(path, dest)
            info = metadata(dest, asset_id)
            self.store.add_asset(project_id, info)
            return info
        for item in result["outputs"]:
            info = register(item["path"])
            source_info = register(item["source_path"]) if item.get("source_path") and item["source_path"] != item["path"] else info
            assets.append((item, info, source_info))
        if assets or result.get("patches") or result.get("project_patch"):
            def change(p):
                for t in p["tracks"]:
                    if t["id"] in result.get("mute_tracks", []) or t["role"] in result.get("mute_roles", []):
                        t["muted"] = True
                        if t["role"] in result.get("mute_roles", []):
                            t["solo"] = False
                for item, asset, root in assets:
                    if item.get("replace"):
                        t = track_by_id(p, item["replace"])
                        t.update(asset_id=asset["id"], duration=asset["duration"], pitch=None)
                        if "transpose" in item:
                            t["transpose"] = item["transpose"]
                    else:
                        t = new_track(asset, item["name"], item["role"], offset=item.get("offset", 0),
                                      muted=item.get("muted", False), gain_db=item.get("gain_db", 0), pan=item.get("pan", 0))
                        if item.get("inherit"):
                            old = track_by_id(p, item["inherit"])
                            t.update(gain_db=old["gain_db"], pan=old["pan"], automation=old["automation"], solo=old["solo"])
                            old["solo"] = False
                        t.update(source_asset_id=root["id"], transpose=item.get("transpose", 0), pitch=item.get("pitch"))
                        p["tracks"].append(t)
                for patch in result.get("patches", []):
                    track_by_id(p, patch["id"]).update({k:v for k,v in patch.items() if k!="id"})
                p.update(result.get("project_patch", {}))
            self.store.mutate(project_id, revision, change, from_job=True)
        if result.get("export"):
            output = Path(result["export"]).resolve()
            if not output.is_relative_to(directory.resolve()) or not output.is_file():
                raise ValueError("Invalid export path")
        return {"export": bool(result.get("export")), "metadata": result.get("metadata", {}),
                "project_revision": self.store.get(project_id)["revision"]}

    def close(self):
        with self.lock:
            self.closed = True
            ids = [j["id"] for j in self.store.jobs() if j["state"] in ACTIVE]
        for job_id in ids:
            self.cancel(job_id)
        self.pool.shutdown(wait=True, cancel_futures=True)
