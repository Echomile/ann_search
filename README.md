# 单细胞高维向量近似最近邻 (ANN) 检索系统

> 软件工程课程大作业 · 面向单细胞测序数据的可视化 ANN 检索平台。

## 项目简介

随着单细胞测序技术的发展，一次实验可产生数十万级别的细胞样本，每个样本经数值化后即为一个高维向量。传统精确最近邻搜索在高维大规模数据上效率低下，本系统基于 **近似最近邻 (ANN)** 算法（HNSW、IVF、PQ 等）实现端到端的细胞相似性检索流水线：

- 支持 `.h5ad` 单细胞数据的上传、读取与预处理；
- 支持多种 ANN 索引的构建、保存、加载与切换；
- 提供 Top-K 相似细胞检索、条件检索（按细胞类型 / 疾病等过滤）、跨数据集联合检索；
- 内置可视化展示（UMAP / t-SNE 投影、检索结果高亮、性能指标）；
- 评测多种距离度量与索引算法的召回率与查询延迟；
- 加分功能：RAG + LLM 自然语言查询、自定义改进的 ANN 算法。

## 技术栈

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 前端 | React 19 · TypeScript · Vite · Ant Design · Zustand · Plotly.js | SPA、状态管理、交互式可视化 |
| 后端 | Python 3.12 · FastAPI · SQLAlchemy 2 (async) · Pydantic v2 · Alembic | 异步 REST API + ORM + 迁移 |
| 任务队列 | ARQ + Redis | 索引构建 / 数据预处理后台异步任务 |
| 数据库 | PostgreSQL 17 | 元数据、用户、数据集、索引、检索记录 |
| 缓存 | Redis 7 | 任务队列与查询结果缓存 |
| ANN 引擎 | FAISS · HNSWLIB · scikit-learn (brute-force baseline) | IVF / HNSW / PQ / Flat |
| 单细胞分析 | scanpy · anndata · numpy · scipy · scikit-learn | h5ad 读取、PCA / UMAP |
| 基础设施 | Docker Compose · Nginx · GitHub Actions · pre-commit | 一键启动、CI、代码规范 |
| 包管理 | uv (后端) · pnpm/npm (前端) | 快速可复现安装 |

## 系统架构

```mermaid
flowchart LR
  subgraph Client[浏览器 / 前端]
    UI[React + AntD<br/>可视化页面]
  end

  subgraph Edge[反向代理]
    NX[Nginx]
  end

  subgraph App[应用层]
    API[FastAPI<br/>REST API]
    WK[ARQ Worker<br/>异步任务]
  end

  subgraph Engine[ANN 引擎]
    FAISS[FAISS · HNSWLIB]
  end

  subgraph Data[数据层]
    PG[(PostgreSQL<br/>元数据)]
    RD[(Redis<br/>队列 / 缓存)]
    FS[/本地文件系统<br/>data · indexes/]
  end

  UI -- HTTPS --> NX
  NX -- /api --> API
  NX -- /ws --> API
  API <-->|enqueue| RD
  RD --> WK
  API <--> PG
  WK <--> PG
  API --> FAISS
  WK --> FAISS
  WK <--> FS
  API <--> FS
```

## 项目结构

```
ann_search/
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── api/                # 路由层
│   │   ├── core/               # 配置 / 安全 / 日志
│   │   ├── db/                 # 数据库 session 与 base
│   │   ├── models/             # SQLAlchemy ORM
│   │   ├── schemas/            # Pydantic 模型
│   │   ├── services/           # 业务服务（数据集 / 索引 / 检索）
│   │   └── tasks/              # ARQ 异步任务
│   ├── alembic/                # 数据库迁移
│   └── tests/
├── frontend/                   # React + TS 前端
│   ├── src/
│   │   ├── api/                # axios 客户端 (自动生成 / 手写)
│   │   ├── components/         # 通用组件
│   │   ├── pages/              # 页面
│   │   ├── router/             # 路由
│   │   ├── store/              # Zustand 状态
│   │   └── types/              # 类型定义
│   └── public/
├── infra/                      # 基础设施
│   ├── docker-compose.yml      # 生产编排
│   ├── docker-compose.dev.yml  # 开发覆盖（热重载）
│   └── nginx/nginx.conf        # 反向代理
├── data/                       # h5ad 原始 / 预处理数据 (gitignored)
├── indexes/                    # ANN 索引文件 (gitignored)
├── docs/                       # 软件开发文档（5 篇）
├── .github/workflows/          # GitHub Actions CI
├── .env.example                # 环境变量样例
├── Makefile                    # 便捷命令
└── README.md
```

## 快速开始

### 1. Docker Compose 一键启动 (推荐)

```bash
git clone <repo-url>
cd ann_search

cp .env.example .env          # 按需修改 SECRET_KEY / LLM_API_KEY
make up                       # 启动 postgres + redis + backend + worker + frontend

make migrate                  # 应用数据库迁移 (首次运行)
make logs                     # 查看运行日志
```

启动后访问：

- 前端 UI: <http://localhost:5173>
- 后端 API 文档 (Swagger): <http://localhost:8000/docs>
- 后端 API 文档 (ReDoc): <http://localhost:8000/redoc>
- PostgreSQL: `localhost:5432` (账号: `ann` / `ann`)
- Redis: `localhost:6379`

停止服务：

```bash
make down
```

### 2. 本地开发（不使用 Docker）

后端：

```bash
cd backend
uv sync                              # 安装 Python 依赖
docker compose -f ../infra/docker-compose.yml up -d postgres redis
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

前端：

```bash
cd frontend
pnpm install                          # 或 npm install
pnpm dev                              # http://localhost:5173
```

ARQ Worker：

```bash
cd backend
uv run arq app.tasks.worker.WorkerSettings
```

## 加分功能

- **多数据集联合检索**：支持上传多个 `.h5ad` 数据集并合并构建联合索引，跨数据集返回 Top-K。
- **ANN 算法改进**：基线提供 FAISS-Flat / FAISS-IVF / HNSW，并在 `services/index_engines/` 下扩展自研算法，统一评测召回率、QPS、内存占用。
- **RAG + 单细胞 LLM 问答**：通过自然语言（如「肝脏中类似肝细胞的细胞」）触发语义解析 -> 元数据过滤 -> ANN 检索 -> LLM 总结的混合检索流程。

## 常用命令

```bash
make help            # 列出全部命令
make up              # 启动开发栈 (热重载)
make down            # 停止并移除
make logs            # 查看日志
make ps              # 查看状态
make backend-shell   # 进入 backend 容器
make db-shell        # 进入 psql
make migrate         # alembic upgrade head
make test            # 运行前后端测试
make lint            # 代码检查
make format          # 自动格式化
```

## 开发规范

- 后端：ruff (lint + format) · mypy (类型) · pytest (测试)；命名采用 `snake_case`，类用 `PascalCase`。
- 前端：ESLint · Prettier · TypeScript strict；组件用 `PascalCase`，hooks 用 `useXxx`。
- Git：约定式提交 (`feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `chore:`)，pre-commit 自动校验。
- 文档：见 [`docs/`](docs/) 目录，含项目概述、需求与设计、测试、项目管理、用户手册。

## 团队分工

| 成员 | 角色 | 主要职责 |
| --- | --- | --- |
| TBD | 项目经理 / 后端 | 项目管理、API 设计、ANN 引擎集成 |
| TBD | 后端 / 算法 | 数据预处理、索引构建、检索服务 |
| TBD | 前端 / 可视化 | 页面与交互、Plotly 可视化 |
| TBD | 测试 / 运维 | 测试用例、CI、Docker 编排 |

详见 [`docs/04_项目管理.md`](docs/04_项目管理.md)。

## License

仅用于课程作业，未对外发布前请勿用于生产。
