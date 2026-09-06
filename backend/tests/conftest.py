import io
import os
import tempfile
import numpy as np
import pytest
import soundfile as sf

# The import-time ASGI app must not create personal data inside the source tree.
os.environ.setdefault("KTV_DATA_DIR", tempfile.mkdtemp(prefix="ktv-import-tests-"))
os.environ.setdefault("KTV_MODELS_DIR", tempfile.mkdtemp(prefix="ktv-model-tests-"))
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def sine(seconds=2, hz=220, amplitude=.15, stereo=True):
    time = np.arange(round(48000 * seconds)) / 48000
    signal = amplitude*np.sin(2*np.pi*hz*time)
    return np.column_stack([signal, signal]).astype(np.float32) if stereo else signal.astype(np.float32)


def wav_bytes(seconds=2, hz=220):
    data = io.BytesIO()
    sf.write(data, sine(seconds, hz), 48000, format="WAV", subtype="FLOAT")
    return data.getvalue()


@pytest.fixture
def source(tmp_path):
    path = tmp_path/"source.wav"
    sf.write(path, sine(), 48000, subtype="FLOAT")
    return path


@pytest.fixture
def client(tmp_path):
    config = Settings(data=tmp_path/"data", models=tmp_path/"models", max_upload=2*1024*1024, job_timeout=60)
    app = create_app(config)
    with TestClient(app, headers={"X-KTV-Client": "1"}) as test:
        yield test


def import_project(client, seconds=2):
    response = client.post("/api/projects/import", files={"file": ("fixture.wav", wav_bytes(seconds), "audio/wav")})
    assert response.status_code == 201, response.text
    return response.json()
