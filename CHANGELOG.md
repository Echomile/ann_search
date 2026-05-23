# 更新日志 (CHANGELOG)

本项目遵循 [约定式提交 (Conventional Commits)](https://www.conventionalcommits.org/zh-hans/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

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
