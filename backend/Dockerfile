# 单细胞 ANN 检索系统后端镜像
# 支持 multi-arch（linux/amd64, linux/arm64）：base 镜像 python:3.12-slim 原生跨架构
# 通过 docker buildx --platform 选择目标架构，不在此 FROM 上写死 --platform=
FROM python:3.12-slim AS base

# buildx 自动注入：TARGETPLATFORM/TARGETARCH/BUILDPLATFORM/BUILDARCH
ARG TARGETPLATFORM
ARG BUILDPLATFORM
ARG TARGETARCH

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libgomp1 \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml ./
RUN uv pip install --system -e .

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
