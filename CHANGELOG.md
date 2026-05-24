# 更新日志 (CHANGELOG)

本项目遵循 [约定式提交 (Conventional Commits)](https://www.conventionalcommits.org/zh-hans/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [v1.2.0-alpha.1] - 2026-05-24

v1.1.0 之后的 **M1 性能呈现升级**：6 个语义化 commit（feat 3 / docs 1 / chore 2 / 含 Phase 0 初始化），新增 4 个 REST 接口（3 个 sweep + 1 个 with_params）+ 2 张新表 + 1 个前端 Tab + 1 张 PPT 增量页，全部基于 `feat/v1.2-bonus` 分支。本里程碑覆盖 v1.2 路线图的 6 项加分功能中的前 2 项（C3 帕累托曲线 / D1 交互式仪表盘）。

### 新功能 Features

#### v1.2 加分项 C3 · ANN-Benchmarks 风格 recall-QPS 帕累托曲线

- **feat(eval) M1.C3**：`param_sweep()` 服务 + `SweepRun` / `SweepPoint` ORM + alembic `0002_v1_2_sweep_tables` migration（commit `4529a27`）。
- 新增 3 个 REST 接口：
  - `POST /api/v1/evaluation/sweep` 同步触发参数扫描（小规模 <30s 内完成）。
  - `GET  /api/v1/evaluation/sweep/{id}` 拉取全部数据点（按 recall 升序）。
  - `GET  /api/v1/evaluation/sweep/{id}/pareto` 仅返回前沿子集。
- 帕累托标记算法 `_mark_pareto()` 在 (recall, qps) 双目标空间上扫一遍标记，时间复杂度 O(N²)。
- 默认扫描栅格：hnswlib / faiss-hnsw / adaptive-hnsw 用 `ef_search ∈ {16,32,64,128,256,512}`；faiss-ivfpq 用 `nprobe ∈ {4,8,16,32,64,128}`；brute 单点。

#### v1.2 加分项 D1 · 交互式参数仪表盘后端

- **feat(search) M1.D1**：`POST /api/v1/search/with_params` 端点（commit `f42e2ae`）。
- 在不重建索引的前提下透传 `runtime_params` 到 backend（hnswlib/faiss-hnsw/adaptive-hnsw 的 `ef_search`、faiss-ivfpq 的 `nprobe`），用 try/finally 保证查询结束后参数恢复，避免污染 IndexCache 给后续普通查询。
- 响应体回填 `effective_params` + `ignored_params`，便于前端展示生效参数与被忽略的 key。

#### 前端

- **feat(frontend) M1**：`EvaluationPage` 改造为 Tabs 结构，新增「参数扫描 (v1.2)」Tab（commit `85dedc9`）。
- 新增 `frontend/src/components/evaluation/SweepTab.tsx`（583 行）：触发表单 + recall-QPS 帕累托散点图（按 backend 分组着色 + 前沿大星标 + 虚线连线）+ 散点点击反查 → 滑块联动 + 参数滑块（`ef_search` 8-512 / `nprobe` 1-256）+ 选中点详情面板 + 实时 Top-K 预览（debounce 200ms）。
- `PlotlyChart` 组件扩展 `onClick` / `onHover` prop 透传 + 对应 TypeScript 类型 export。
- 新增 `frontend/src/types/evaluation.ts` 中 `SweepRequest / SweepPoint / SweepRun`，`frontend/src/types/search.ts` 中 `SearchWithParamsRequest / SearchResponseWithParams`，`frontend/src/api/evaluation.ts` 中 3 个 sweep 方法，`frontend/src/api/search.ts` 中 `withParams` 方法。

### 文档 Docs

- **docs(benchmark) M1.C3**：`docs/benchmark_report.md` §7 新增「recall-QPS 帕累托曲线分析」章节（占位数据待真实 sweep 跑通后用 regex 批量回填）+ 新增 `docs/slides/v1_2_increment_draft.md` PPT 增量页 Marp markdown 草稿（commit `d8f5ae9`）。

### 工程 Engineering

- **chore(v1.2)** Phase 0：进度追踪 `docs/v1.2_progress.json`（3 milestone × 12 task）+ Loop 状态 `docs/_loop_status.md`（commit `f4d713d`）。
- **chore(format)**：ruff 自动格式化 6 个无关文件（多行函数签名 / 字符串合并为单行，commit `bd094b1`）。
- **执行机制**：Pattern B 阶段化并行（milestone 间串行 + milestone 内 2~3 个 subagent 并行）+ 全程 `/loop 5m` 后台 polish 监督（pytest / vitest / lint 自动回归）。

### 工程指标 (v1.1.0 → v1.2.0-alpha.1)

| 维度 | v1.1.0 | v1.2.0-alpha.1 | 增量 |
| --- | ---: | ---: | --- |
| 后端 pytest | 76 | **86** | +10（sweep + with_params 测试） |
| 前端 vitest | 42 | **42** | 0（SweepTab 单测留 alpha.2 补） |
| REST 接口 | 31+ | **35+** | +4（3 sweep + 1 with_params） |
| Alembic 迁移版本 | 1 | **2** | +1（0002 sweep tables） |
| 前端 Tabs | 0 | **1** | +1（评测 / 参数扫描） |
| 前端组件 | — | **+SweepTab** | 583 行新组件 |

### M1 后续 polish 待办

- 跑真实 sweep（liver.h5ad PCA 30 维，5 backend × 6 params ≈ 30 数据点）。
- 用真实数据 regex 回填 `docs/benchmark_report.md` §7 占位（`0.99XX` / `XXXXX`）。
- 生成 3 张静态 PNG 帕累托曲线图嵌入文档与 PPT。
- 视频补录 SweepTab 交互演示（30 秒）。
- 给 SweepTab 写 vitest 单测，目标覆盖触发 / 散点反查 / 滑块联动。

[v1.2.0-alpha.1]: https://github.com/aokimi/ann_search/releases/tag/v1.2.0-alpha.1

## [v1.1.0] - 2026-05-24

v1.0.0 之后的 **feat + perf + polish 平衡升级**：32 个语义化 commit（feat 15 / test 5 / docs 5 / perf 2 / chore 2 / build 2 / ci 1），新增 8 项功能 / 4 项性能优化 / 4 个 E2E 流程 / 4 张 PPT 演进页 / 3 张关键截图，全部基于 `develop` 分支，向下兼容 v1.0.0 接口。

### 新功能 Features

#### 后端接口（8 项加分功能）

- **feat(search) F1**：批量检索 API `POST /search/batch` + Redis 缓存复用，单次最多 64 个查询，命中缓存零计算（commit `c61d975`）。
- **feat(cache) F2**：Redis 检索结果缓存（服务层 + 接入 by-id / by-vector 调用链 + 与 IndexCache 合并 metrics，5 单测覆盖；commit `132592c` / `a4c998a` / `8754f9c`）。
- **feat(perf) F3**：索引 mmap 加载 + 向量 float16 可选落盘，大索引冷启动内存减半（commit `b755c9c` / `b755c9c` 之 F5 部分）。
- **feat(perf) F4**：启动预热 IndexCache，消除首查冷启动 50~200 ms（commit `85dd898`）。
- **feat(perf) F5**：向量 float16 可选落盘，索引体积减半（与 F3 同 commit）。
- **feat(search) F6**：SSE 流式 by-vector 检索接口 `POST /search/stream`，浏览器逐条吐结果，无需等待全部 Top-K 完成（commit `2bbf30a`）。
- **feat(search) F7**：ensemble 多后端融合检索 `POST /search/ensemble`，z-score 归一化 + 加权融合 hnswlib / faiss / brute 结果（commit `54727a7`）。
- **feat(rag) F8**：Anthropic Claude Opus LLM 客户端 + 工厂分支，`LLM_PROVIDER=anthropic` 切换；新增 RAG 单测（commit `177a029`）。

#### 前端体验

- **feat(ui) B1**：`plotly-basic-dist-min` 替换全量 plotly，包体 4.47 MB → 1.07 MB（commit `28e04ba`）。
- **feat(ui) B2**：移动端 Drawer 响应式布局 + B3 全站 Loading skeleton 替换 spin（commit `07bed9c`）。
- **feat(ui)**：SearchPage 接入 F6 SSE 流式 + F7 ensemble 多后端 Tab，所见即所得（commit `ebfac47`）。
- **feat(ui)**：IndexDetailPage 索引详情独立页 + IndexCache 命中率展示。
- **feat(ui)**：Admin 用户管理页（管理员 CRUD + 重置密码 + 状态切换）。

#### 运维与统计

- **feat(cache) C1**：`GET /indexes/cache/stats` IndexCache 命中率统计（commit `e310a95`）。
- **feat(indexes) C4**：`GET /indexes/{id}/latest-benchmark` 索引视角读最近评测（commit `1959592`）。
- **feat(datasets) C3**：`PATCH /datasets/{id}` 数据集重命名接口（commit `2d9c0b4`）。

### 性能优化 Performance

- **perf(backend) P2**：`numba` 加速 BruteBackend 暴力检索，3.15× 提速（commit `fc3d688`）。
- **perf(backend) P3**：SQLAlchemy `selectinload` 预加载消除数据集列表 N+1 查询（同 commit `fc3d688`）。
- **perf(http) P4**：brotli / gzip 响应压缩中间件，大 JSON 响应体减少 70%+（commit `f6cfd6f`）。
- **perf(benchmark) P1**：N=100k 大规模真机实测 + 报告更新（commit `cc706ed`）。

### 文档 Docs

- **docs(api)**：`docs/06_API接口文档.md` 同步 v1.1 新增 10 个接口（21 → 31；commit `2ad6b5e`）。
- **docs(slides) A3**：答辩 PPT v2 增加 4 张 v1.1 演进页（21 → 25 张；commit `ea79546`）。
- **docs(arch) A1**：架构图导出 PNG/SVG 嵌入 README/02 设计文档（含 system_overview / overall_architecture / search_pipeline / er_diagram / task_state_machine / usecase 共 6 张；commit `46ce8b7`）。
- **docs(readme) A4**：README 新增 Troubleshooting 与 FAQ 两章 16 个常见问题（commit `d917f18`）。
- **docs(benchmark)**：N=100k 大规模实测报告更新（与 P1 同 commit）。

### 测试 Tests

- **test(frontend) D1**：Vitest 单测脚手架 + utils/error / utils/metadata 10 用例（commit `6d05089`）；后续拓展 utils/format +13 用例（commit `b55841a`）。
- **test(frontend) D4**：hooks/usePolling + store/authStore / datasetStore 单测拓展（与 commit `28e04ba` 同 commit），最终 vitest **23 → 42** 用例。
- **test(backend)**：F2 Redis 缓存 5 单测 + F8 Anthropic 客户端单测 + IndexCache stats 单测，pytest **47 → 76** 用例。
- **test(e2e) D2**：新增 admin / upload-progress / stats / rag 四个 Playwright 流程（commit `681715d`），后续修复跑通 + 截图归档（commit `8f4ab73` / `d085454`）。

### 构建与 CI Build / Chore

- **ci(frontend) D3**：CI frontend job 新增 Vitest 单元测试步骤（commit `8a7eb2b`）。
- **chore(format) E3**：全仓格式化对齐 ruff format / prettier，CI 加 prettier check（commit `ae17760`）。
- **chore(stats)**：清理重复 stats router + 修复滚动 24h 桶 bug（commit `6ee31c2`）。
- **build(backend)**：同步 `uv.lock`（anthropic + numba 等新依赖；commit `8791d2a`）。

### v1.1.0 实测改进总结

| 维度 | v1.0.0 | v1.1.0 | 增量 |
| --- | ---: | ---: | --- |
| 后端 pytest | 47 | **76** | +29（含 F2 缓存 / F8 Anthropic / IndexCache 等） |
| 前端 vitest | 23 | **42** | +19（D1 + D4 hooks / store 拓展） |
| E2E 流程脚本 | 1（liver） | **5** | +4（admin / upload / stats / rag） |
| REST 接口 | 21 | **31+** | +10（F1/F2/F6/F7 + C1/C3/C4 等） |
| 答辩 PPT 张数 | 21 | **25** | +4 v1.1 演进页 |
| 真实数据截图 | 9 | **14** | +5（admin / dashboard / IndexDetail / 100k / multi） |
| BruteBackend 检索 | 1.0× | **3.15×** | P2 numba JIT |
| 前端 plotly 包体 | 4.47 MB | **1.07 MB** | B1 basic-dist 瘦身 |

[v1.1.0]: https://github.com/aokimi/ann_search/releases/tag/v1.1.0

## [v1.0.0] - 2026-05-23

第一个正式发布版本，覆盖软件工程大作业全部课程要求与三项加分项。

### 新功能 Features

#### 后端核心模块
- **feat(backend)**: 搭建 FastAPI 后端骨架与目录结构
- **feat(backend)**: 实现用户认证模块（JWT + 注册/登录 + Alembic 初始迁移）
- **feat(backend)**: 数据集管理与 Scanpy 预处理模块
- **feat(backend)**: ANN 索引构建模块与 4 种后端引擎（hnswlib / faiss-hnsw / faiss-ivfpq / brute）
- **feat(backend)**: 检索与性能评测模块（条件过滤 + 多数据集联合 + Recall/QPS/延迟）
- **feat(backend)**: RAG 自然语言查询模块（加分项，Mock / DashScope / OpenAI 三客户端）
- **feat(backend)**: 自适应 HNSW 后端（加分项，自适应 ef_search + 早停 + 升档）
- **feat(stats)**: SearchLog 历史可视化 Dashboard（C1）
- **feat(admin)**: 管理员用户 CRUD + 重置密码 + 用户管理页（C4）
- **feat(upload)**: 后端写盘进度 API + 前端 Steps 双进度条（C2）
- **feat(datasets)**: 数据集去重 + 孤儿清理 + 同名 409（A3）

#### 前端
- **feat(frontend)**: 搭建 React 18 + Vite 5 + AntD + Plotly 前端工程
- **feat(frontend)**: 五个业务页面（数据集 / 索引 / 检索 / 可视化 / 评测）
- **feat(frontend)**: RAG 自然语言对话页（加分项前端）
- **refactor(frontend)**: bundle 拆分 + extractError 抽取 + 索引详情页（B1+B3+C3）
- **feat(ui)**: 真实 UMAP 可视化 + Metadata 折叠 + Plotly 布局微调（A1+A2+A5）

#### 演示与文档
- **feat(demo)**: 自动化生成 5'54" 带中文配音演示视频（含片头/片尾静态卡）
- **feat(demo)**: 多数据集联合检索 e2e 截图（C5）
- **docs(slides)**: 课程答辩 PPT 21 张（Marp Markdown + PDF + PPTX）
- **docs**: 扩充五份课程开发文档 + 新增 API 接口速查表
- **docs(submission)**: 提交物索引清单 (MANIFEST.md)
- **test(e2e)**: Playwright 端到端真实数据测试 + 10 张验收截图

#### 工程与基础设施
- **build(infra)**: Docker Compose + Makefile + Pre-commit + GitHub Actions CI
- **perf(worker)**: ARQ 启动时预热 umap-learn JIT，消除首次预处理 60 秒卡顿
- **chore(make)**: 新增 e2e / demo-video / slides / benchmark / submission / screenshots 6 个便捷命令

### 修复 Bug Fixes

- **fix(backend)**: CORS_ORIGINS 支持逗号分隔字符串（pydantic-settings NoDecode）
- **fix(frontend)**: 文件上传 422 错误（FormData 自动剥离 Content-Type）
- **fix(frontend)**: 登录请求与认证字段对齐后端
- **fix(ui+demo)**: 索引页参数列单行显示 + 真实交互截图

### 实测性能

- liver.h5ad（69 032 cells × 30 维 X_pca）
- hnswlib 索引构建 **0.22 秒**，内存 16.3 MB
- by-id 检索延迟 **0.47 ms**（端到端 90 ms）
- Recall@10 = **99.90%**，Recall@100 = **99.64%**
- 峰值 **60 k QPS**
- FAISS-IVFPQ 内存最低 **0.29 MB**（节省 24x）

### 工程指标

- 后端 35 → 47 个 pytest 用例全部通过
- ruff / ESLint 全过
- 41 个语义化 git commit
- 21 张答辩 PPT
- 10 张 UI 真实数据截图
- 5'54" 中文配音演示视频
- 21 个 REST 接口（基础 + admin + stats）
- 7 份开发文档 + 性能基准报告 + 提交物清单

### 加分项交付（全部完成）

- 多数据集联合检索：`POST /api/v1/search/multi-dataset`
- ANN 算法改进：`AdaptiveHnswBackend`（自适应 ef）
- RAG 自然语言查询：`POST /api/v1/rag/query`

[v1.0.0]: https://github.com/aokimi/ann_search/releases/tag/v1.0.0
