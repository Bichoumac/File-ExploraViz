# syntax=docker/dockerfile:1
# File ExploraViz — multi-stage image (python:3.12-slim), runs as non-root through PUID/PGID.

ARG PYTHON_VERSION=3.12

# ---------------------------------------------------------------- stage 1: dependencies
FROM python:${PYTHON_VERSION}-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt /tmp/requirements.txt
# Binary wheels only (no compiler in the image), then slimming: tests, typing stubs, C headers
# and numpy's f2py are not needed at runtime.
RUN pip install --prefix=/install --only-binary=:all: -r /tmp/requirements.txt \
 && find /install -depth -type d -name tests -prune -exec rm -rf {} + \
 && find /install -name '*.pyi' -delete \
 && rm -rf /install/lib/python*/site-packages/numpy/_core/include \
           /install/lib/python*/site-packages/numpy/f2py \
           /install/lib/python*/site-packages/numpy/_pyinstaller \
           /install/bin

# ---------------------------------------------------------------- stage 2: final image
FROM python:${PYTHON_VERSION}-slim

ARG DEBIAN_FRONTEND=noninteractive
# tzdata: local time (TZ variable) for logs, history and the off-peak window.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /var/log/apt /var/log/dpkg.log

COPY --from=builder /install /usr/local
WORKDIR /opt/exploraviz
COPY app ./app
COPY web ./web

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Paris \
    PORT=8080 \
    PUID=1000 \
    PGID=1000 \
    LOG_LEVEL=INFO \
    AUTH_DISABLED=false \
    FEV_CONFIG_DIR=/config \
    FEV_CACHE_DIR=/cache \
    FEV_DATA_DIR=/data \
    FEV_WEB_DIR=/opt/exploraviz/web

# The process starts as root only to hand /config and /cache over to PUID:PGID,
# then drops its privileges for good (see app/entrypoint.py).
RUN mkdir -p /config /cache /data && python -m compileall -q app
VOLUME ["/config", "/cache"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT','8080'), timeout=4)" || exit 1

LABEL org.opencontainers.image.title="File ExploraViz" \
      org.opencontainers.image.description="Self-hosted, read-only file explorer and visualiser" \
      org.opencontainers.image.source="https://github.com/Bichoumac/File-ExploraViz"

ENTRYPOINT ["python", "-m", "app.entrypoint"]
