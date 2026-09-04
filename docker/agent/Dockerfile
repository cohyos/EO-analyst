# syntax=docker/dockerfile:1.7
# docker/agent/Dockerfile
#
# Orchestrator/agent image: runs `python -m eoa.orchestrator.main`.
# No browser automation, no dev tooling — kept as small as possible since it
# is the long-running, internet-isolated core process (see docker-compose.yml
# and scripts/verify_isolation.*).
#
# Build context MUST be the repo root (not this directory), because the
# package build needs pyproject.toml, agent/ and README.md:
#   docker compose build agent   # context: ., dockerfile: docker/agent/Dockerfile

# ---------------------------------------------------------------------------
# Stage 0: bake the L1 prompt-injection classifier (Protect AI DeBERTa v2,
# protectai/deberta-v3-base-prompt-injection-v2, Apache-2.0) into the image as
# ONNX weights, at BUILD time — this stage has internet access, the final
# `agent` container does not (docker-compose.yml sets `dns: [0.0.0.0]` on it).
#
# Prefers the model repo's own published `onnx/` folder (present as of
# 2026-09; ships model.onnx + tokenizer files) so no local export/conversion
# is needed — this common path needs only `huggingface_hub` (small). Falls
# back to an `optimum-cli` export if a future repo revision ever drops that
# folder; only in that branch does this stage pip-install `optimum`/
# `transformers` (CPU-only torch, same `--index-url` trick as Stage 1 below —
# this discarded builder stage doesn't affect final image size, but there's
# no reason to pull a CUDA wheel here either).
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS guard-model

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir "huggingface_hub>=0.24"

RUN python3 <<'PY'
import os
import shutil
import subprocess
import sys

from huggingface_hub import list_repo_files, snapshot_download

MODEL_ID = "protectai/deberta-v3-base-prompt-injection-v2"
DEST = "/opt/models/prompt-guard"
os.makedirs(DEST, exist_ok=True)

files = list_repo_files(MODEL_ID)
if any(f.startswith("onnx/") for f in files):
    print(f"[guard-model] {MODEL_ID}: onnx/ folder present on the hub -- downloading it")
    tmp = "/tmp/hf_dl"
    snapshot_download(MODEL_ID, allow_patterns=["onnx/*"], local_dir=tmp)
    onnx_dir = os.path.join(tmp, "onnx")
    for name in os.listdir(onnx_dir):
        shutil.move(os.path.join(onnx_dir, name), os.path.join(DEST, name))
else:
    print(f"[guard-model] {MODEL_ID}: no onnx/ folder on the hub -- exporting at build time")
    # CPU-only torch first (same trick as Stage 1) so `optimum`'s own
    # `torch>=1.11` dependency resolves against this small wheel instead of
    # pulling the multi-GB CUDA build.
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-cache-dir",
         "--index-url", "https://download.pytorch.org/whl/cpu", "torch"],
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-cache-dir",
         "optimum[exporters,onnxruntime]>=1.21", "transformers>=4.44"],
        check=True,
    )
    subprocess.run(
        ["optimum-cli", "export", "onnx", "--model", MODEL_ID, "--task", "text-classification", DEST],
        check=True,
    )

assert os.path.isfile(os.path.join(DEST, "model.onnx")), f"guard model bake failed: no model.onnx in {DEST}"
print("[guard-model] bake complete:", sorted(os.listdir(DEST)))
PY

# ---------------------------------------------------------------------------
# Stage 1: the actual agent image.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Jerusalem \
    # L1 guard: loaded fully offline from the baked model below (see
    # agent/eoa/security/guard.py::_l1_pipeline) — no CUDA torch wheel, no
    # runtime network call.
    EOA_GUARD_L1_DIR=/opt/models/prompt-guard \
    HF_HUB_OFFLINE=1

WORKDIR /app

# libpq-dev: psycopg[binary] ships its own libpq, but keep build tooling
# available for any source-built wheels (e.g. on platforms without prebuilt
# binary wheels).
RUN apt-get update \
    && apt-get install -y --no-install-recommends --no-install-suggests \
       build-essential \
       libpq-dev \
       tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# Copy only what the package build needs first, for layer caching.
COPY pyproject.toml README.md ./
COPY agent/ ./agent/

# guard-onnx: optimum[onnxruntime] + transformers — deliberately not the
# `guard` extra (plain torch>=2.3), which pulls the default CUDA-enabled
# torch wheel (multi-GB) this CPU-only classifier doesn't need.
#
# `optimum` itself still declares a bare `torch>=1.11` dependency even for
# the onnxruntime-only path (ORTModelForSequenceClassification needs torch
# tensors for pre/post-processing, not for inference), so install the small
# CPU-only build from PyTorch's own CPU wheel index FIRST — once a torch
# satisfying that constraint is present, the second install below is a no-op
# for torch and never reaches for the CUDA one.
RUN uv pip install --system --index-url https://download.pytorch.org/whl/cpu torch \
    && uv pip install --system -e ".[guard-onnx]"

# Baked L1 model (see Stage 0 above) — the only way EOA_GUARD_L1_DIR resolves
# to real weights inside this internet-isolated container.
COPY --from=guard-model /opt/models/prompt-guard /opt/models/prompt-guard

# Runtime mount points (see docker-compose.yml): config is read-only,
# output is where reports/exports land.
RUN mkdir -p /app/config /app/output

CMD ["python", "-m", "eoa.orchestrator.main"]
