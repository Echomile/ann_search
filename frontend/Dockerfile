# 多阶段构建：node 构建静态产物，nginx 提供服务
# 支持 multi-arch（linux/amd64, linux/arm64）：node:22-alpine 与 nginx:1.27-alpine 均原生跨架构
# 由 docker buildx --platform 选择目标架构，不在此 FROM 上写死 --platform=
FROM node:22-alpine AS builder

# buildx 自动注入：TARGETPLATFORM/TARGETARCH/BUILDPLATFORM/BUILDARCH
ARG TARGETPLATFORM
ARG BUILDPLATFORM

WORKDIR /app

COPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* pnpm-workspace.yaml* ./
RUN if [ -f pnpm-lock.yaml ]; then \
      corepack enable && pnpm install --frozen-lockfile; \
    elif [ -f yarn.lock ]; then \
      yarn install --frozen-lockfile; \
    else \
      npm install --no-audit --no-fund; \
    fi

COPY . .
RUN npm run build

# 开发阶段：复用 builder 已安装的依赖，运行 Vite dev server（热重载）。
# 由 infra/docker-compose.dev.yml 通过 target: dev 选用，源码与 node_modules 在容器内挂载/复用。
FROM builder AS dev
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]

# 生产阶段：nginx 提供静态产物，作为最后一个 stage 被未显式指定 target 的 infra/docker-compose.yml 默认选用。
FROM nginx:1.27-alpine AS runner
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
