# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json ./
RUN npm install --ignore-scripts --no-audit --no-fund
COPY frontend/ ./
RUN npm run build && npm test

FROM python:3.11-slim-bookworm AS common
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    KTV_DATA_DIR=/data KTV_MODELS_DIR=/models KTV_STATIC_DIR=/app/frontend/dist \
    PYTHONPATH=/app/backend TORCH_HOME=/models/torch HF_HOME=/models/huggingface \
    NUMBA_CACHE_DIR=/tmp/numba MPLCONFIGDIR=/tmp/matplotlib \
    OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 NUMBA_NUM_THREADS=4
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg librubberband2 libsndfile1 libsamplerate0 libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt && pip check
RUN useradd --create-home --uid 1000 ktv && mkdir -p /data /models /app/build-info \
    && chown -R ktv:ktv /data /models /app
COPY --chown=ktv:ktv backend/ /app/backend/
COPY --chown=ktv:ktv scripts/doctor.py /app/scripts/doctor.py
COPY --from=frontend --chown=ktv:ktv /build/dist /app/frontend/dist
COPY LICENSE THIRD_PARTY_NOTICES.md /app/
RUN pip freeze > /app/build-info/requirements-resolved.txt \
    && python -c "from app.stretch import library; library(); import parselmouth, pedalboard"
EXPOSE 7860
HEALTHCHECK --interval=15s --timeout=15s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/api/health', timeout=12)" || exit 1
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1", "--no-access-log"]

FROM common AS cpu
USER ktv

FROM common AS gpu
USER root
# Legacy upstream diffq/samplerate may compile inside this build stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake pkg-config libsamplerate0-dev \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir torch==2.10.0 torchaudio==2.10.0 torchvision==0.25.0 \
    --index-url https://download.pytorch.org/whl/cu128
# Build the legacy NumPy extension against the already installed NumPy. A build
# failure is explicit; never replace the pinned CUDA wheels with CPU wheels.
RUN pip install --no-cache-dir setuptools wheel Cython \
    && pip install --no-cache-dir --no-build-isolation diffq==0.2.4 samplerate==0.1.0 \
    && pip install --no-cache-dir -c backend/constraints-gpu.txt -r backend/requirements-ai.txt \
    && pip check \
    && python -c "import torch, torchaudio, torchcrepe; from audio_separator.separator import Separator; assert torch.version.cuda == '12.8'" \
    && pip freeze > /app/build-info/requirements-resolved.txt
USER ktv
