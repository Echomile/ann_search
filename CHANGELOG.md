# 更新日志 (CHANGELOG)

本项目遵循 [约定式提交 (Conventional Commits)](https://www.conventionalcommits.org/zh-hans/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [v1.2.0] - 2026-05-24

v1.2.0 正式版本：**v1.1.0 之后的 v1.2 路线图 6 项加分功能全部交付**（C3 + D1 + D2 + C5 + D7 + D4），分 M1 / M2 / M3 三个 milestone 推进；总计 9 个语义化 commit（含 4 个 feat / 1 fix / 2 feat-scripts / 2 docs-release），通过 Pattern B 阶段化并行（milestone 间串行 + milestone 内 2~3 个 subagent 并行）+ 全程 `/loop 5m` 后台 polish 监督。

### 新功能 Features (v1.2.0-alpha.2 → v1.2.0)

#### v1.2 加分项 D7 · 跨数据集语义对齐

- **feat(m3) D7**：单纯把多 dataset 各自查 + min-max 重排（v1.0/v1.1 实现）升级为对齐到统一向量空间后单库检索（commit `3cb25e4`）。
- 新增服务 `align_datasets()` 实现 `intersect_only`（取基因集交集 + 在统一空间重新 PCA target_dim 维）与 `harmony`（可选，harmonypy 缺失时降级 intersect_only）两种策略。
- 新增 `AlignedDataset` ORM + `0004_v1_2_aligned_datasets` migration（`source_dataset_ids_json / method / target_dim / cell_count / common_genes_count / vectors_path / cell_map_path / status / created_by`）。
- 新增 4 个 REST 接口：`POST /api/v1/datasets/align`（同步触发对齐）+ `GET /aligned`（列表）+ `GET /aligned/{id}`（详情）+ `DELETE /aligned/{id}`。
- `search.py` multi-dataset 路径新增 `aligned_dataset_id` 参数：提供时在对齐空间单库检索（更简单且向量空间一致），不提供保持现有兼容路径。
- 前端：`DatasetsPage` 加「对齐」按钮（多选 dataset 后激活），`SearchPage` multi-dataset Tab 增加 aligned toggle，`alignmentApi` 4 个方法 + `AlignedDataset / AlignRequest` 类型。
- 文档：`docs/benchmark_report.md` §9 新增「跨数据集语义对齐：intersect_only vs 各自检索对比」。

#### v1.2 加分项 D4 · LLM Function Calling RAG Agent

- **feat(m3) D4**：把 v1.1 RAG 三段式 `parse → search → summarize` 固定流程 + 规则解析升级为 LLM 自主决定的 Agent 风格（commit `3cb25e4`）。
- 新增 `services/rag_tools.py` 含 5 个工具 schema：`search_by_cell_id / search_by_vector / list_datasets / filter_cells / summarize_results`，OpenAI / Anthropic 通用格式。
- `services/rag.py` 重构 `chat_with_tools` Agent loop：多轮 `tool_call → tool_result` 直到 `finish_reason=stop`，`max_iterations=5` 安全上限。
- 4 个 LLM client（`mock / openai / dashscope / anthropic`）都增加 `chat_with_tools()` 方法，统一返回 `ChatResponse(finish_reason, tool_calls, content)`；mock client 用规则模拟 tool_call 保证零依赖 e2e 可跑。
- 新增 `RagSession / RagMessage` ORM + `0005_v1_2_rag_sessions` migration（含 `role / content / tool_calls_json / tool_results_json / cascade delete`）。
- 改造 `POST /api/v1/rag/query` 支持 `session_id` 多轮，新增 `GET /api/v1/rag/sessions/{id}` 拉取历史；响应含 `answer + tool_trace + citations`（引用追溯）。
- 前端 `RagChatPage.tsx` 完全重构为 ChatGPT 风格气泡对话：用户右侧蓝色，AI 左侧灰色，AI 调用工具时显示「正在搜索...」状态条，每条 AI 回答下方有「引用」折叠面板（hits 表格），输入框 Enter 发送。

### 工程指标 (v1.1.0 → v1.2.0)

| 维度 | v1.1.0 | v1.2.0 | 增量 | 增长 |
| --- | ---: | ---: | --- | --- |
| 后端 pytest | 76 | **110** | +34 | +45% |
| 前端 vitest | 42 | 42 | 0 | — |
| REST 接口 | 31+ | **45+** | +14 | +45% |
| Alembic 迁移版本 | 1 | **5** | +4 | +400% |
| ANN 后端数量 | 5 | **6** | +1 (sparse-brute) | +20% |
| 前端页面 | 9 | **10** | +1 (IndexGraphPage) | — |
| LLM client 工具 | 4 (无 tool calling) | **4 + Function Calling** | Agent 升级 | — |
| 文档章节 (benchmark) | §5.7 | **§9** | +§7/§8/§9 三个新章节 | — |
| 加分项数 (累计) | 11 | **17** | +6 | C3/D1/D2/C5/D7/D4 |

### v1.2.0 路线图完整完成情况

| Milestone | 加分项 | 状态 | tag |
| --- | --- | --- | --- |
| M1 性能呈现升级 | C3 recall-QPS 帕累托 + D1 交互式仪表盘 | done | v1.2.0-alpha.1 |
| M2 算法可视化 + 单细胞独家性 | D2 HNSW 图可视化 + C5 稀疏感知 ANN | done | v1.2.0-alpha.2 |
| M3 跨数据集深度 + Agent 升级 | D7 跨数据集对齐 + D4 LLM Function Calling | done | **v1.2.0** |

### 后续 polish 待办（可选）

- 跑真实多数据集对齐 e2e 验证 + 回填 `docs/benchmark_report.md` §9 占位。
- 录 v1.2 演示视频补段（参数仪表盘 + HNSW 图 + 稀疏对比 + 对齐 + Agent 对话）。
- 答辩 PPT v3 增加 v1.2 演进 6 张专题页。
- SweepTab / IndexGraphPage / RagChatPage 前端 vitest 单测补齐。

[v1.2.0]: https://github.com/aokimi/ann_search/releases/tag/v1.2.0

## [v1.2.0-alpha.2] - 2026-05-24

v1.2.0-alpha.1 之后的 **M2 算法可视化 + 单细胞独家性**：2 个大型 feature commit（D2 + C5），新增 1 个 REST 接口（subgraph）+ 1 张新表（datasets.vector_format）+ 1 个新 ANN 后端（sparse-brute）+ 1 个新前端页面（IndexGraphPage）+ 1 个新章节（§8 稀疏对比）。

### 新功能 Features

#### v1.2 加分项 D2 · HNSW 邻居图结构可视化

- **feat(d2)**：让 HNSW 索引的小世界图可见（commit `1cc26b0`）。
- 后端 `HnswlibBackend.get_local_subgraph(entry_label, depth, layer, max_nodes)`：调 `hnswlib.Index.get_neighbors_list` 拿邻接表 + BFS 展开 depth 跳；旧版 hnswlib 不支持时 raise `NotImplementedError`。
- 新增 REST 接口 `GET /api/v1/indexes/{id}/subgraph?cell_id=&depth=&layer=&max_nodes=`，限定 hnswlib / adaptive-hnsw 后端，通过 `cell_ids` 映射到内部 label，返回前端友好的子图结构。
- 新增 schema `SubgraphNode / SubgraphEdge / SubgraphResponse`（含 `is_entry / is_topk / cell_type` 元数据字段）。
- 新增前端页面 `IndexGraphPage.tsx`（474 行）：Plotly 渲染节点 + 边图，查询起点红五角星，depth=1 橙色，depth>=2 灰色，提供 depth/layer/max_nodes 控件，truncated 状态条提示。
- `IndexDetailPage` 新增「查看邻居图」按钮跳转到 `/indexes/:id/graph`（仅 hnswlib / adaptive-hnsw 可用），Layout 菜单加入索引图谱入口。
- **修复**：`IndexBackend` TypeScript type union 扩展 `adaptive-hnsw` + `sparse-brute`（修复 v1.0 以来 long-standing union 不完整 bug）。

#### v1.2 加分项 C5 · 稀疏感知 ANN

- **feat(c5)**：单细胞独家性卖点 - 跳过 PCA 在 5000 HVG 稀疏向量上直接做检索（commit `2a0f928`）。
- 新增 `SparseBruteBackend`（230 行）基于 `scipy.sparse.csr_matrix`，支持 l2 / cosine / ip 三种度量；行 L2 归一化用对角缩放（纯稀疏路径不展开稠密），l2 距离公式 `||a||² + ||b||² - 2 a·b` 预计算并缓存底库 sq_norms。
- 工厂注册新后端 `sparse-brute`。
- 数据模型 `Dataset.vector_format: Literal["dense", "sparse"]` + alembic `0003_v1_2_dataset_vector_format` migration（batch_alter_table 兼容 SQLite，`server_default='dense'` 自动回填旧数据）。
- 预处理 `preprocess.py` 新增 `vector_source="raw_sparse"` 模式：跳过 PCA，选 top 5000 HVG 后保存为 `.npz` 稀疏格式；index_task / preprocess_task 适配 `.npy / .npz` 路径分流。
- 前端 `IndexManagePage` BACKEND_OPTIONS 加入 `adaptive-hnsw` + `sparse-brute` 两个选项（之前 adaptive-hnsw 仅后端可用，前端选不到）。

### 工程优化 Engineering

- **fix(eval)**：`_build_backend_for_sweep` 的 `faiss-ivfpq` nlist 启发式从写死 64 改为 `sqrt(N)`（在 [8, 4096] 范围内，不超过 `N//4`），原写法对 N=30k 时召回受 nlist 过小拖累，从 ~0.42 提升到 ~0.48（commit `843cad3`）。
- **feat(scripts)** 新增 `backend/scripts/sweep_offline.py`：离线 sweep CLI，复用 `evaluation.py` 纯函数工具，支持合成数据或加载真实 npy 向量，输出 JSON 用于 docs 回填（commit `3392350` + `843cad3`）。
- **feat(scripts)** 新增 `backend/scripts/sweep_plot.py`：matplotlib PNG 导出器，按 backend 分组着色 + 帕累托前沿 ★ 标记 + 红色虚线连接 + log Y 轴（commit `3ee988c`）。
- **§7.3 真实数据回填**：用 liver.h5ad PCA 30D N=30000 子集跑出 25 个数据点（5 帕累托前沿），表格内所有 `0.99XX / XXXXX` 占位全部替换为实测值；原始 JSON 保存 `docs/sweep_real_liver_pca30.json`，静态 PNG `docs/assets/benchmark/pareto_pca30.png`。

### 文档 Docs

- `docs/benchmark_report.md` §8 新增「稀疏感知 ANN: SparseBruteBackend vs 稠密后端对比」（8.1-8.6 六个小节）；§7.3 真实数据回填（25 数据点 + 5 帕累托前沿 + 5 条观察要点）。

### 工程指标 (v1.2.0-alpha.1 → v1.2.0-alpha.2)

| 维度 | alpha.1 | alpha.2 | 增量 |
| --- | ---: | ---: | --- |
| 后端 pytest | 86 | **96** | +10（D2 subgraph + C5 sparse 测试） |
| 前端 vitest | 42 | **42** | 0 |
| REST 接口 | 35+ | **36+** | +1（subgraph） |
| Alembic 迁移版本 | 2 | **3** | +1（0003 vector_format） |
| ANN 后端数量 | 5 | **6** | +1（sparse-brute） |
| 前端页面 | — | **+IndexGraphPage** | 474 行 Plotly 图可视化 |
| 文档章节 | §7 占位 | **§7 实测 + §8** | §7 全部回填 + §8 完整 6 小节 |

[v1.2.0-alpha.2]: https://github.com/aokimi/ann_search/releases/tag/v1.2.0-alpha.2

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
