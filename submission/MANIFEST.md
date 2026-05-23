# 提交物清单 (Submission Manifest)

**项目**：单细胞高维向量近似最近邻 (ANN) 检索系统  
**课程**：软件工程大作业  
**仓库分支**：`develop`  
**当前 tag**：`v1.1.0`（2026-05-24，基于 v1.0.0 的 feat+perf+polish 平衡升级）  
**提交日期**：2026 年 5 月

本清单按 `project.md` 中"团队作业结项提交"要求逐项列出全部交付物，所有文件均位于仓库根目录的相对路径下，可直接在 GitHub Markdown 中点击访问。

> **快速浏览版本演进**：[`CHANGELOG.md`](../CHANGELOG.md) 列出 v1.0.0 与 v1.1.0 全量 commit；本清单聚焦"提交了什么"。

---

## 〇、版本与里程碑

| 版本 | 日期 | 概要 | 关键产出 |
| --- | --- | --- | --- |
| **v1.0.0** | 2026-05-23 | 课程要求 + 三项加分项首版交付 | 21 接口 / 21 张 PPT / 9 张截图 / 5'54" 演示视频草稿 |
| **v1.1.0** | 2026-05-24 | feat + perf + polish 平衡升级 | **31+ 接口 / 25 张 PPT / 14 张截图 / 2'42" 演示视频 / 6 张架构图 / 76 pytest + 42 vitest** |

---

## 一、源代码

| 模块 | 路径 | 说明 |
| --- | --- | --- |
| 后端 | [`backend/`](../backend/) | FastAPI 0.118+ · 异步 SQLAlchemy 2 · PostgreSQL 17 · Redis 7 · ARQ 任务队列 · 5 种 ANN 后端 |
| 前端 | [`frontend/`](../frontend/) | React 18 · Vite 5 · TypeScript 5 · Ant Design 5 · Plotly · Zustand · 8 个业务页面 |
| 基础设施 | [`infra/`](../infra/) | Docker Compose · Nginx |
| 数据库迁移 | [`backend/alembic/`](../backend/alembic/) | 4 张表 ER：users · datasets · index_records · search_logs |
| 端到端测试 | [`e2e/`](../e2e/) | Playwright 真实数据集联调脚本 + 演示视频自动录制 |
| CI/CD | [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) | 后端 ruff+pytest · 前端 lint+build |
| 依赖锁定 | `backend/uv.lock` · `frontend/pnpm-lock.yaml` | 完全可复现安装 |

## 二、开发文档（五份课程要求 + 三份补充）

| # | 文档 | 路径 |
| - | --- | --- |
| 1 | 项目概述 | [`docs/01_项目概述.md`](../docs/01_项目概述.md) |
| 2 | 需求分析与系统设计（含 6 张架构图） | [`docs/02_需求分析与系统设计.md`](../docs/02_需求分析与系统设计.md) |
| 3 | 系统测试 | [`docs/03_系统测试.md`](../docs/03_系统测试.md) |
| 4 | 项目管理 | [`docs/04_项目管理.md`](../docs/04_项目管理.md) |
| 5 | 用户手册（含 FAQ：admin / SSE / ensemble） | [`docs/05_用户手册.md`](../docs/05_用户手册.md) |
| 6 | API 接口文档（**31+** 接口） | [`docs/06_API接口文档.md`](../docs/06_API接口文档.md) |
| 7 | 性能基准实验报告（含 N=100k 大规模实测） | [`docs/benchmark_report.md`](../docs/benchmark_report.md) |
| 8 | 更新日志（v1.0.0 + v1.1.0） | [`CHANGELOG.md`](../CHANGELOG.md) |

### 2.1 架构图（v1.1.0 新增 A1）

| 文件 | 大小 | 内容 |
| --- | ---: | --- |
| [`docs/assets/architecture/system_overview.png`](../docs/assets/architecture/system_overview.png) · `.svg` | 78 KB | 系统总体架构（前端 / Nginx / FastAPI / Worker / PG / Redis / FS） |
| [`docs/assets/architecture/overall_architecture.png`](../docs/assets/architecture/overall_architecture.png) · `.svg` | 135 KB | 详细分层架构（API / 服务层 / ANN 引擎层） |
| [`docs/assets/architecture/search_pipeline.png`](../docs/assets/architecture/search_pipeline.png) · `.svg` | 106 KB | 检索流水线（请求 → 缓存 → ANN → 过滤 → 排序 → 响应）|
| [`docs/assets/architecture/er_diagram.png`](../docs/assets/architecture/er_diagram.png) · `.svg` | 29 KB | 4 张表 ER 图（users · datasets · index_records · search_logs） |
| [`docs/assets/architecture/task_state_machine.png`](../docs/assets/architecture/task_state_machine.png) · `.svg` | 18 KB | ARQ 任务状态机（pending → building → ready / failed） |
| [`docs/assets/architecture/usecase.png`](../docs/assets/architecture/usecase.png) · `.svg` | 139 KB | UML 用例图 |

> 源 `.mmd` 一并提交，`bash docs/assets/architecture/export_mermaid.sh` 可一键重新渲染。

## 三、答辩 PPT（v1.1.0 25 张）

| 文件 | 大小 | 用途 |
| --- | ---: | --- |
| [`docs/slides/answer_defense.pdf`](../docs/slides/answer_defense.pdf) | ~1.8 MB | 适合投屏 / 打印（**25 张**，含 v1.1 演进 4 张专题页） |
| [`docs/slides/answer_defense.pptx`](../docs/slides/answer_defense.pptx) | ~5.8 MB | 适合 PowerPoint 编辑 |
| [`docs/slides/answer_defense.md`](../docs/slides/answer_defense.md) | 27 KB | Marp Markdown 源（一键重新生成） |
| [`docs/slides/speaker_notes.md`](../docs/slides/speaker_notes.md) | 11 KB | 配音讲稿（每页对应口语化中文） |

## 四、演示视频

| 文件 | 时长 | 分辨率 | 说明 |
| --- | ---: | --- | --- |
| [`docs/video/demo_final.mp4`](../docs/video/demo_final.mp4) | 2'42" | 1440×900 | H.264 + AAC · 11 段中文配音 · Playwright 自动驱动浏览器全流程（含登录 / 上传 / 索引 / 检索 / 可视化 / 评测 / RAG） |

录制脚本：[`e2e/demo_video.py`](../e2e/demo_video.py)，可一键重新生成。

## 五、实测验证

| 项目 | 路径 | 说明 |
| --- | --- | --- |
| 端到端真实数据测试 | [`e2e/test_liver_e2e.py`](../e2e/test_liver_e2e.py) | Playwright 注入 1.3 GB liver.h5ad，跑通登录→上传→预处理→索引→检索→可视化→评测→RAG 全链路 |
| Admin E2E（v1.1 D2） | [`e2e/test_admin_e2e.py`](../e2e/test_admin_e2e.py) | `/admin/users` CRUD + 重置密码全流程 |
| Upload-Progress E2E（v1.1 D2） | [`e2e/test_upload_progress_e2e.py`](../e2e/test_upload_progress_e2e.py) | 双进度条 + threading 高频轮询 |
| Stats E2E（v1.1 D2） | [`e2e/test_stats_e2e.py`](../e2e/test_stats_e2e.py) | 评测后 SearchLog Dashboard 渲染 |
| RAG E2E（v1.1 D2） | [`e2e/test_rag_e2e.py`](../e2e/test_rag_e2e.py) | 自然语言查询 + hits 表格 |
| 验收截图（**14 张**） | [`docs/e2e_screenshots/`](../docs/e2e_screenshots/) | 含 v1.1 新增：admin / SearchLog Dashboard / IndexDetail / multi-dataset 等 |
| 性能基准数据 | [`docs/benchmark_results.json`](../docs/benchmark_results.json) | 5 后端 × N × dim × top_k × concurrency 完整性能数据（含 N=100k） |
| 基准脚本 | [`backend/scripts/benchmark.py`](../backend/scripts/benchmark.py) | argparse CLI · 自动生成报告 |
| 后端 pytest（76 用例） | [`backend/tests/`](../backend/tests/) | 含 F2 Redis 缓存 / F8 Anthropic / IndexCache / SearchCache / Adaptive HNSW 单测 |
| 前端 vitest（42 用例） | [`frontend/src/**/*.test.ts*`](../frontend/src/) | utils / hooks / stores 单测 |

## 六、加分功能交付

### 6.1 课程要求三项加分（v1.0.0 已完成）

| 加分项 | 主要实现位置 | 说明 |
| --- | --- | --- |
| 多数据集联合检索 | [`backend/app/api/v1/search.py`](../backend/app/api/v1/search.py) · `multi_dataset_search` | `asyncio.gather` 并发查多索引 + min-max 归一化 + 重排 |
| ANN 算法改进 | [`backend/app/services/ann/adaptive_hnsw_backend.py`](../backend/app/services/ann/adaptive_hnsw_backend.py) | 自适应 ef_search：首轮 gap 早停 + 升档至上限 512 |
| RAG 自然语言查询 | [`backend/app/services/rag.py`](../backend/app/services/rag.py) · [`backend/app/api/v1/rag.py`](../backend/app/api/v1/rag.py) | parse → search → summarize；Mock / 通义千问 / OpenAI / **Anthropic Claude** 四客户端 |

### 6.2 v1.1.0 工程优化八项（F1~F8）

| 编号 | 功能 | 接口 / 实现 | 说明 |
| :---: | --- | --- | --- |
| **F1** | 批量检索 + 缓存复用 | `POST /api/v1/search/batch` | 单次最多 64 查询，命中缓存零计算 |
| **F2** | Redis 检索结果缓存 | [`backend/app/services/search/cache.py`](../backend/app/services/search/cache.py) + `GET /search/cache/stats` | by-id / by-vector 全链路缓存，命中率可查 |
| **F3** | 索引 mmap 加载 | [`backend/app/services/index_cache.py`](../backend/app/services/index_cache.py) | 大索引冷启动内存减半 |
| **F4** | 启动预热 IndexCache | [`backend/app/tasks/worker.py`](../backend/app/tasks/worker.py) `on_startup` | 消除首查冷启动 50~200 ms |
| **F5** | 向量 float16 落盘 | [`backend/app/services/preprocess.py`](../backend/app/services/preprocess.py) | 向量体积减半 |
| **F6** | SSE 流式检索 | `POST /api/v1/search/stream` | 浏览器逐条吐结果 |
| **F7** | ensemble 多后端融合 | `POST /api/v1/search/ensemble` | z-score 归一化 + 加权融合 hnswlib / faiss / brute |
| **F8** | Anthropic Claude LLM | [`backend/app/services/llm/`](../backend/app/services/llm/) | `LLM_PROVIDER=anthropic`，Claude Opus 接入 RAG |

### 6.3 v1.1.0 性能与运维优化（P1~P4 + C1/C3/C4）

| 编号 | 优化 | 接口 / 实现 | 收益 |
| :---: | --- | --- | --- |
| **P1** | N=100k 大规模基准实测 | [`docs/benchmark_report.md`](../docs/benchmark_report.md) | 真实硬件吞吐验证 |
| **P2** | numba 加速 BruteBackend | [`backend/app/services/ann/brute_backend.py`](../backend/app/services/ann/brute_backend.py) | **3.15×** 提速 |
| **P3** | SQLAlchemy 预加载 | `selectinload` 在数据集 / 索引列表查询 | 消除 N+1 查询 |
| **P4** | brotli / gzip 响应压缩 | [`backend/app/main.py`](../backend/app/main.py) GzipMiddleware | 大 JSON 响应体减少 70%+ |
| **C1** | IndexCache 命中率统计 | `GET /api/v1/indexes/cache/stats` | LRU 缓存可观测 |
| **C3** | 数据集重命名 | `PATCH /api/v1/datasets/{id}` | 新接口 |
| **C4** | 索引视角评测 | `GET /api/v1/indexes/{id}/latest-benchmark` | 索引页直读最近评测 |

### 6.4 v1.1.0 前端 / 测试 / 文档

| 编号 | 范围 | 说明 |
| :---: | --- | --- |
| **B1** | plotly 包体瘦身 | 4.47 MB → **1.07 MB**（-76%） |
| **B2** | 移动响应式 Drawer | `frontend/src/layout/` |
| **B3** | Loading skeleton | 全站 Spin → Skeleton |
| **D1+D4** | Vitest 单测 | 23 → **42** 用例 |
| **D2** | E2E 流程 | +4 个 Playwright 流程（admin / upload / stats / rag） |
| **D3** | CI Vitest 步骤 | `.github/workflows/ci.yml` |
| **A1** | 6 张架构图 | `docs/assets/architecture/*.{png,svg,mmd}` |
| **A3** | PPT v2 | 21 → **25** 张（+4 v1.1 演进页） |
| **A4** | README + FAQ | Troubleshooting 8 条 + FAQ 8 条 |
| **E3** | 全仓格式化 | ruff format + prettier check |

## 七、关键运行命令

```bash
# 一键启动（推荐）
cp .env.example .env
make up                # postgres + redis + backend + worker + frontend
make migrate           # alembic upgrade head

# 端到端测试（真实数据）
cd backend && uv run python ../e2e/test_liver_e2e.py

# 重新生成 PPT（PDF + PPTX）
marp docs/slides/answer_defense.md -o docs/slides/answer_defense.pdf
marp docs/slides/answer_defense.md -o docs/slides/answer_defense.pptx

# 重新生成演示视频
cd backend && uv run python ../e2e/demo_video.py

# 跑性能基准
cd backend && uv run python scripts/benchmark.py --n 30000 --dim 30 --use-liver
```

## 八、Git 提交记录

`develop` 分支累计 67+ 个语义化 commit，遵循 Conventional Commits 规范；两个里程碑 tag：

```
git tag -l 'v*'      # → v1.0.0, v1.1.0
git log v1.0.0..v1.1.0 --oneline
```

| 版本 | 范围 | commit 数 | 主要类别 |
| --- | --- | ---: | --- |
| v1.0.0 | 初次发布 | 41 | feat: 21 · fix: 4 · docs: 8 · chore: 3 · build: 3 · test: 2 |
| **v1.1.0** | v1.0.0..v1.1.0 | **32** | feat: 15 · test: 5 · docs: 5 · perf: 2 · chore: 2 · build: 2 · ci: 1 |

## 九、访问地址（启动后）

| 入口 | URL | 说明 |
| --- | --- | --- |
| 前端 SPA | http://localhost:5173 | 用户主界面 |
| 后端 Swagger | http://localhost:8000/docs | **31+** 个接口交互式文档 |
| 后端 ReDoc | http://localhost:8000/redoc | 只读 API 文档 |
| OpenAPI 原始 JSON | http://localhost:8000/openapi.json | 用于客户端生成 |

测试账号：`demo` / `demo1234`（注册接口可自由创建）。

---

> 本清单为提交物索引，所有内容已纳入 Git 版本控制，可通过 `git log` 追溯每次变更。如需打包成单个压缩包，可在仓库根目录执行：  
> ```bash
> git archive --format=zip --prefix=ann_search/ HEAD -o submission/source.zip
> ```
