# 多阶段构建：node 构建静态产物，nginx 提供服务
# 支持 multi-arch（linux/amd64, linux/arm64）：node:22-alpine 与 nginx:1.27-alpine 均原生跨架构
# 由 docker buildx --platform 选择目标架构，不在此 FROM 上写死 --platform=
FROM node:22-alpine AS builder

# buildx 自动注入：TARGETPLATFORM/TARGETARCH/BUILDPLATFORM/BUILDARCH
ARG TARGETPLATFORM
ARG BUILDPLATFORM

WORKDIR /app

COPY package.json package-lock.json* pnpm-lock.yaml* yarn.lock* ./
RUN if [ -f pnpm-lock.yaml ]; then \
      corepack enable && pnpm install --frozen-lockfile; \
    elif [ -f yarn.lock ]; then \
      yarn install --frozen-lockfile; \
    else \
      npm install --no-audit --no-fund; \
    fi

COPY . .
RUN npm run build

FROM nginx:1.27-alpine AS runner
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
