# Multi-arch Docker 镜像构建指南

本指南介绍如何为 `ann-search` 的 backend 与 frontend 镜像构建 `linux/amd64` + `linux/arm64` 双架构镜像，便于在 x86 服务器与 Apple Silicon / ARM 服务器之间统一部署。

---

## 1. 背景与目标

| 维度        | 说明                                                                                                     |
| ----------- | -------------------------------------------------------------------------------------------------------- |
| 现状        | `docker compose build` 仅产出当前 host 架构的镜像（amd64 机器只能跑出 amd64，arm64 同理）                |
| 目标        | 通过 `docker buildx` 一次构建产出同时含 `linux/amd64`、`linux/arm64` 的多架构 manifest，统一推送到 registry |
| 受益        | Apple Silicon 开发机可直接拉取 arm64 层、生产服务器拉取 amd64 层；同一个 tag 全平台可用                  |

镜像层面：

- `backend/Dockerfile` → `python:3.12-slim`（官方 multi-arch base，含 amd64、arm64）
- `frontend/Dockerfile` → `node:22-alpine` + `nginx:1.27-alpine`（同样官方 multi-arch）

两个 Dockerfile 都**未写死** `--platform=linux/amd64`，并显式声明了 `ARG TARGETPLATFORM / BUILDPLATFORM`，由 `docker buildx` 自动注入目标架构信息。

---

## 2. 前置条件

1. Docker Engine ≥ 23.0（自带 buildx 插件）。若 `docker buildx version` 报 `unknown command`，需安装独立 buildx 插件，或升级 Docker Desktop / Docker Engine。
2. 已启用 QEMU 用户态模拟（macOS/Windows Docker Desktop 默认启用；Linux 上若需跨架构构建，执行一次：

   ```bash
   docker run --privileged --rm tonistiigi/binfmt --install all
   ```

   ）

3. （可选，仅推送时需要）已 `docker login` 到目标 registry，例如 `ghcr.io`。

---

## 3. 快速开始

### 3.1 使用 Makefile target

```bash
# 多架构构建（默认 linux/amd64,linux/arm64），仅构建不推送
make docker-buildx

# 仅构建当前 host 架构并 --load 到本地（便于本机 docker run 验证）
make docker-buildx-local

# 多架构构建并推送到 registry
make docker-buildx-push REGISTRY=ghcr.io/<org>/<repo> TAG=v1.0.0
```

可覆盖的环境变量：

| 变量           | 默认值                            | 说明                              |
| -------------- | --------------------------------- | --------------------------------- |
| `PLATFORMS`    | `linux/amd64,linux/arm64`         | buildx 目标平台，逗号分隔         |
| `REGISTRY`     | `ghcr.io/your-org/ann-search`     | 推送时镜像前缀                    |
| `TAG`          | `dev`                             | 镜像 tag                          |
| `BUILDER_NAME` | `ann-search-builder`              | buildx builder 名（多次复用同一个） |

### 3.2 手动调用 buildx

```bash
# 1. 创建/启用 builder（一次即可）
docker buildx create --name ann-search-builder --use
docker buildx inspect --bootstrap

# 2. 构建并推送 backend
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t ghcr.io/<org>/<repo>/backend:v1.0.0 \
  -f backend/Dockerfile backend \
  --push

# 3. 构建并推送 frontend
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t ghcr.io/<org>/<repo>/frontend:v1.0.0 \
  -f frontend/Dockerfile frontend \
  --push
```

---

## 4. `--load` vs `--push` vs 默认

| 标志            | 行为                                                                                              | 何时用                                            |
| --------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| 默认（都不加）  | 构建产物只在 buildx 内部缓存，不会出现在 `docker images`                                          | 仅做语法 / 多架构构建验证（CI dry-run）           |
| `--load`        | 把产物作为镜像加载到本机 docker daemon                                                            | 本地 `docker run` 测试，**只能单 platform**       |
| `--push`        | 直接推送到 registry，并生成多架构 manifest list                                                   | 正式发布、CI/CD 推送                              |

> 因此 `--platform linux/amd64,linux/arm64 --load` 是**非法组合**——本地无法同时加载两套架构，必须落到 registry。`make docker-buildx-local` 用于本机验证。

---

## 5. buildx 与 docker compose 的关系

`infra/docker-compose.yml` 中的 `build:` 段在执行 `docker compose build` 时走的是**经典 builder**，仅产出 host 架构镜像，与 buildx 是两套机制。两者关系：

| 场景                       | 推荐做法                                                                                                              |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| 本地开发 / 热重载          | `make up`（走 `docker-compose.dev.yml`），无需 multi-arch                                                              |
| 本地生产构建预览           | `make up-prod`，构建当前 host 架构即可                                                                                |
| 团队跨平台部署 / 发布镜像  | `make docker-buildx-push REGISTRY=… TAG=…`，把多架构 manifest 推到 registry，再让目标机器 `docker pull` + compose 启动 |

如果希望让 docker compose 在拉镜像时强制选某个 platform，可在 compose 文件中加：

```yaml
services:
  backend:
    image: ghcr.io/<org>/<repo>/backend:v1.0.0
    platform: linux/amd64    # 强制使用 amd64 层（一般不需要，docker 默认按 host 选）
```

本项目默认不写 `platform`，由 docker 根据宿主机自动挑选合适的 manifest。

---

## 6. CI 集成

`.github/workflows/ci.yml` 增加了 `docker-buildx` job，仅在 push 到 `main` 时执行：

- 使用 `docker/setup-qemu-action` 与 `docker/setup-buildx-action` 准备环境
- 用 `docker/build-push-action` 跑 `linux/amd64,linux/arm64` 的 dry-run（`push: false`），验证 Dockerfile 跨架构可构建
- 启用 GitHub Actions cache（`cache-from/cache-to=type=gha`）加速后续构建

需要正式发布镜像时，可在该 job 中追加 `docker/login-action` 与 `push: true`，并设置 `${{ secrets.GHCR_TOKEN }}` 等凭据。

---

## 7. 常见问题

**Q1. `make docker-buildx` 报 `unknown command: docker buildx`？**

升级 Docker Desktop / Docker Engine 到 ≥ 23.0；或手动安装 buildx 插件：

```bash
mkdir -p ~/.docker/cli-plugins
curl -SL https://github.com/docker/buildx/releases/latest/download/buildx-$(uname -s | tr A-Z a-z)-$(uname -m) \
  -o ~/.docker/cli-plugins/docker-buildx
chmod +x ~/.docker/cli-plugins/docker-buildx
```

**Q2. arm64 构建特别慢？**

在 amd64 宿主上构建 arm64 走 QEMU 模拟，依赖编译会显著变慢。生产环境建议：

- 推送到 registry 后由真正的 arm64 机器拉取使用；
- 或在 CI 上使用 native arm64 runner（如 GitHub Actions `runs-on: ubuntu-22.04-arm` 等）。

**Q3. 推送后如何验证多架构 manifest 正确？**

```bash
docker buildx imagetools inspect ghcr.io/<org>/<repo>/backend:v1.0.0
```

输出中应能看到 `linux/amd64`、`linux/arm64` 两条 manifest。

**Q4. 本机只想验证 Dockerfile 没写死架构？**

跑 `make docker-buildx-local`，等价于单 platform `--load`，能直接 `docker run` 调试。

---

## 8. 修改清单参考

E2 任务一次性引入：

- `backend/Dockerfile`：加注释 + `ARG TARGETPLATFORM/BUILDPLATFORM/TARGETARCH`，未写死 platform。
- `frontend/Dockerfile`：同上。
- `Makefile`：新增 `docker-buildx`、`docker-buildx-local`、`docker-buildx-push`，并把 `REGISTRY/TAG/PLATFORMS/BUILDER_NAME` 作为可覆盖变量。
- `.github/workflows/ci.yml`：新增 `docker-buildx` job，main 分支 push 时跑 multi-arch dry-run。
- `docs/deploy/multi-arch.md`（本文件）。

`infra/docker-compose.yml` / `docker-compose.dev.yml` 未改动：buildx 与 compose 是独立链路，平时本地开发无需多架构。
