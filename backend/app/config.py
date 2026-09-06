from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    data: Path = Path(os.getenv("KTV_DATA_DIR", "./data")).resolve()
    models: Path = Path(os.getenv("KTV_MODELS_DIR", "./models")).resolve()
    static: Path = Path(os.getenv("KTV_STATIC_DIR", str(Path(__file__).parents[2] / "frontend" / "dist"))).resolve()
    max_upload: int = int(os.getenv("KTV_MAX_UPLOAD_MB", "200")) * 1024 * 1024
    max_duration: int = int(os.getenv("KTV_MAX_DURATION_SECONDS", "1200"))
    require_gpu: bool = os.getenv("KTV_REQUIRE_GPU", "0") == "1"
    job_timeout: int = int(os.getenv("KTV_JOB_TIMEOUT_SECONDS", "7200"))


settings = Settings()
