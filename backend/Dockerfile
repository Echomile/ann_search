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

# 规避 SIGILL：在 macOS Virtualization.Framework 等虚拟化 CPU 上，部分预编译 native 库
# 加载/运行时会执行宿主 CPU 不支持的指令而崩溃（Illegal instruction）：
#   - cryptography 捆绑的 OpenSSL 做 ARM capability 探测 -> OPENSSL_armcap=0 关闭探测；
#   - pynndescent/numba JIT 默认按 host CPU 生成代码 -> NUMBA_CPU_NAME=generic 退回通用目标。
# 二者均改走通用实现，对本项目（低频 JWT 签名、离线预处理）的性能影响可忽略。
ENV OPENSSL_armcap=0 \
    NUMBA_CPU_NAME=generic \
    NUMBA_CPU_FEATURES=""

# 开发阶段：额外安装 dev 依赖（pytest/ruff/mypy 等），默认以热重载方式启动。
# 由 infra/docker-compose.dev.yml 通过 target: dev 选用；源码与依赖在容器内挂载/复用。
FROM base AS dev
RUN uv pip install --system -e ".[dev]"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# 生产阶段：作为最后一个 stage，被未显式指定 target 的 infra/docker-compose.yml 默认选用。
FROM base AS prod
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
