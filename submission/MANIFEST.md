# 提交物清单 (Submission Manifest)

**项目**：单细胞高维向量近似最近邻 (ANN) 检索系统  
**课程**：软件工程大作业  
**仓库分支**：`develop`  
**提交日期**：2026 年 5 月

本清单按 `project.md` 中"团队作业结项提交"要求逐项列出全部交付物，所有文件均位于仓库根目录的相对路径下，可直接在 GitHub Markdown 中点击访问。

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

## 二、开发文档（五份课程要求 + 两份补充）

| # | 文档 | 路径 |
| - | --- | --- |
| 1 | 项目概述 | [`docs/01_项目概述.md`](../docs/01_项目概述.md) |
| 2 | 需求分析与系统设计 | [`docs/02_需求分析与系统设计.md`](../docs/02_需求分析与系统设计.md) |
| 3 | 系统测试 | [`docs/03_系统测试.md`](../docs/03_系统测试.md) |
| 4 | 项目管理 | [`docs/04_项目管理.md`](../docs/04_项目管理.md) |
| 5 | 用户手册 | [`docs/05_用户手册.md`](../docs/05_用户手册.md) |
| 6 | API 接口文档（补充） | [`docs/06_API接口文档.md`](../docs/06_API接口文档.md) |
| 7 | 性能基准实验报告（补充） | [`docs/benchmark_report.md`](../docs/benchmark_report.md) |

## 三、答辩 PPT

| 文件 | 大小 | 用途 |
| --- | ---: | --- |
| [`docs/slides/answer_defense.pdf`](../docs/slides/answer_defense.pdf) | ~1 MB | 适合投屏 / 打印（18 张） |
| [`docs/slides/answer_defense.pptx`](../docs/slides/answer_defense.pptx) | ~4 MB | 适合 PowerPoint 编辑 |
| [`docs/slides/answer_defense.md`](../docs/slides/answer_defense.md) | 19 KB | Marp Markdown 源（一键重新生成） |
| [`docs/slides/speaker_notes.md`](../docs/slides/speaker_notes.md) | 8 KB | 配音讲稿（每页对应口语化中文） |

## 四、演示视频

| 文件 | 时长 | 分辨率 | 说明 |
| --- | ---: | --- | --- |
| [`docs/video/demo_final.mp4`](../docs/video/demo_final.mp4) | 2'42" | 1440×900 | H.264 + AAC · 11 段中文配音 · Playwright 自动驱动浏览器全流程 |

录制脚本：[`e2e/demo_video.py`](../e2e/demo_video.py)，可一键重新生成。

## 五、实测验证

| 项目 | 路径 | 说明 |
| --- | --- | --- |
| 端到端真实数据测试 | [`e2e/test_liver_e2e.py`](../e2e/test_liver_e2e.py) | Playwright 注入 1.3 GB liver.h5ad，跑通登录→上传→预处理→索引→检索→可视化→评测→RAG 全链路 |
| 验收截图 | [`docs/e2e_screenshots/`](../docs/e2e_screenshots/) | 9 张关键操作截图归档 |
| 性能基准数据 | [`docs/benchmark_results.json`](../docs/benchmark_results.json) | 5 后端 × N × dim × top_k × concurrency 完整性能数据 |
| 基准脚本 | [`backend/scripts/benchmark.py`](../backend/scripts/benchmark.py) | argparse CLI · 自动生成报告 |

## 六、加分功能交付（三项全做）

| 加分项 | 主要实现位置 | 说明 |
| --- | --- | --- |
| 多数据集联合检索 | [`backend/app/api/v1/search.py`](../backend/app/api/v1/search.py) · `multi_dataset_search` | `asyncio.gather` 并发查多索引 + min-max 归一化 + 重排 |
| ANN 算法改进 | [`backend/app/services/ann/adaptive_hnsw_backend.py`](../backend/app/services/ann/adaptive_hnsw_backend.py) | 自适应 ef_search：首轮 gap 早停 + 升档至上限 512 |
| RAG 自然语言查询 | [`backend/app/services/rag.py`](../backend/app/services/rag.py) · [`backend/app/api/v1/rag.py`](../backend/app/api/v1/rag.py) | parse → search → summarize；Mock / 通义千问 / OpenAI 三客户端 |

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

`develop` 分支共 25+ 个语义化 commit，遵循 Conventional Commits 规范：

```
git log --oneline develop
```

主要类别：`feat:` 18 个 · `fix:` 4 个 · `docs:` 5 个 · `chore:` 2 个 · `build:` 2 个 · `test:` 1 个

## 九、访问地址（启动后）

| 入口 | URL | 说明 |
| --- | --- | --- |
| 前端 SPA | http://localhost:5173 | 用户主界面 |
| 后端 Swagger | http://localhost:8000/docs | 21 个接口交互式文档 |
| 后端 ReDoc | http://localhost:8000/redoc | 只读 API 文档 |
| OpenAPI 原始 JSON | http://localhost:8000/openapi.json | 用于客户端生成 |

测试账号：`demo` / `demo1234`（注册接口可自由创建）。

---

> 本清单为提交物索引，所有内容已纳入 Git 版本控制，可通过 `git log` 追溯每次变更。如需打包成单个压缩包，可在仓库根目录执行：  
> ```bash
> git archive --format=zip --prefix=ann_search/ HEAD -o submission/source.zip
> ```
