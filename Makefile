# ============================================================
# 单细胞 ANN 检索系统 - 常用开发命令
# 用法: make <target>
# ============================================================

SHELL := /bin/bash

COMPOSE       := docker compose -f infra/docker-compose.yml
COMPOSE_DEV   := docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml

.DEFAULT_GOAL := help

.PHONY: help up up-prod down restart logs ps build rebuild \
        backend-shell worker-shell db-shell redis-shell \
        migrate makemigration test test-backend test-frontend \
        lint lint-backend lint-frontend format format-backend format-frontend \
        install install-backend install-frontend clean clean-cache prune \
        e2e screenshots demo-video slides submission benchmark

help: ## 列出全部可用命令
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ============ Docker Compose ============
up: ## 启动开发栈（含热重载）
	$(COMPOSE_DEV) up -d

up-prod: ## 启动生产栈
	$(COMPOSE) up -d

down: ## 停止并移除所有容器
	$(COMPOSE) down

restart: ## 重启全部服务
	$(COMPOSE_DEV) restart

logs: ## 跟随查看全部日志
	$(COMPOSE) logs -f

ps: ## 查看服务状态
	$(COMPOSE) ps

build: ## 构建镜像
	$(COMPOSE) build

rebuild: ## 强制无缓存重新构建
	$(COMPOSE) build --no-cache

# ============ 进入容器 ============
backend-shell: ## 进入 backend 容器
	$(COMPOSE) exec backend /bin/bash

worker-shell: ## 进入 worker 容器
	$(COMPOSE) exec worker /bin/bash

db-shell: ## 进入 PostgreSQL psql
	$(COMPOSE) exec postgres psql -U ann -d ann

redis-shell: ## 进入 Redis redis-cli
	$(COMPOSE) exec redis redis-cli

# ============ 数据库迁移 ============
migrate: ## 应用 Alembic 迁移到最新
	$(COMPOSE) exec backend alembic upgrade head

makemigration: ## 生成新的迁移文件 m="msg"
	$(COMPOSE) exec backend alembic revision --autogenerate -m "$(m)"

# ============ 测试 ============
test: test-backend test-frontend ## 运行全部测试

test-backend: ## 运行后端测试
	$(COMPOSE) exec backend pytest -q

test-frontend: ## 运行前端测试
	cd frontend && npm test --silent || pnpm test

# ============ 代码质量 ============
lint: lint-backend lint-frontend ## 运行 lint

lint-backend:
	cd backend && uv run ruff check . && uv run ruff format --check .

lint-frontend:
	cd frontend && npm run lint --if-present

format: format-backend format-frontend ## 自动格式化

format-backend:
	cd backend && uv run ruff format . && uv run ruff check --fix .

format-frontend:
	cd frontend && npm run format --if-present

# ============ 本地安装 ============
install: install-backend install-frontend ## 一次性本地安装依赖

install-backend:
	cd backend && uv sync

install-frontend:
	cd frontend && npm install

# ============ 清理 ============
clean: ## 清理本地构建产物
	rm -rf backend/.venv backend/.pytest_cache backend/.ruff_cache backend/.mypy_cache
	rm -rf frontend/node_modules frontend/dist frontend/.vite

clean-cache: ## 清理 Python / 前端缓存
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +

prune: ## 移除 docker 卷与镜像（危险）
	$(COMPOSE) down -v --remove-orphans
	docker system prune -f

# ============ 课程交付物 ============
e2e: ## 跑端到端真实数据测试（注入 liver.h5ad）
	cd backend && uv run python ../e2e/test_liver_e2e.py

screenshots: ## 重新生成 9 张端到端 UI 实测截图
	cd backend && uv run python ../e2e/capture_screenshots.py

demo-video: ## 自动录制带中文配音的演示视频 -> docs/video/demo_final.mp4
	cd backend && uv run python ../e2e/demo_video.py

slides: ## 重新生成答辩 PPT (PDF + PPTX) by Marp
	marp docs/slides/answer_defense.md -o docs/slides/answer_defense.pdf --allow-local-files
	marp docs/slides/answer_defense.md -o docs/slides/answer_defense.pptx --allow-local-files

benchmark: ## 跑性能基准并自动生成报告
	cd backend && uv run python scripts/benchmark.py --n 30000 --dim 30 --use-liver

submission: ## 打包源代码归档到 submission/source.zip
	git archive --format=zip --prefix=ann_search/ HEAD -o submission/source.zip
	@echo "  -> submission/source.zip ($$(du -h submission/source.zip | cut -f1))"
