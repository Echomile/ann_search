---
marp: true
paginate: true
size: 16:9
header: ann_search v1.2 增量页
---

# v1.2 加分: recall-QPS 帕累托曲线

## 业界标准 ANN 评估范式 (ANN-Benchmarks 风格)

[页面布局占位]

- **左半**: Plotly 帕累托散点图（5 backend, 每个 6-7 点）
- **右半**:
  - 帕累托前沿 = 不被任何其他点同时在 recall 和 QPS 上 dominate
  - 算法对比一目了然：
    - hnswlib 在 high-recall 区间领先
    - faiss-ivfpq 在 low-recall + 高 QPS 区间领先
    - adaptive-hnsw 自适应触发后逼近最优
  - 后端新增 `POST /sweep` + 2 个 GET 共 3 个接口
  - 前端 `EvaluationPage` 新增「参数扫描」Tab，鼠标点击点 → 滑块联动 + 右侧实时 Top-K

## 数据 (待回填实测)

| backend | best (recall, qps) | 前沿点数 |
| --- | --- | --- |
| hnswlib | (X.XX, XXXXX) | X |
| faiss-hnsw | (X.XX, XXXXX) | X |
| faiss-ivfpq | (X.XX, XXXXX) | X |
| adaptive-hnsw | (X.XX, XXXXX) | X |
| brute | (1.00, 1657) | 1 |

---

# 实现要点 (待主代理整合时展开)

## 后端 (M1-α)

- 数据库新增 `sweep_run` / `sweep_point` 两张表，迁移见 `backend/alembic/versions/`
- 服务层 `backend/app/services/evaluation.py::run_parameter_sweep()`
  - 输入：`backend` 列表 + 各 backend 的参数网格
  - 输出：每个 (backend, params) 组合的 `recall@K`, `qps`, `p50_ms`, `p95_ms`, `on_pareto`
  - 复用既有的 `EvaluationService` 内核（保证与 §4 / §5.7 数据可比）
- 帕累托标记：`_mark_pareto(points: list[SweepPoint]) -> None` 单调扫描 O(N log N)
- API 路由 `backend/app/api/v1/evaluation.py`
  - `POST /api/v1/evaluation/sweep` 启动扫描任务，返回 `sweep_run_id`
  - `GET  /api/v1/evaluation/sweep/{id}` 查询状态 + 全部点
  - `GET  /api/v1/evaluation/sweep/{id}/pareto` 仅返回前沿点（便于前端轻量拉取）

## 前端 (D1)

- `frontend/src/pages/EvaluationPage.tsx` 新增「参数扫描」Tab
- 左侧 Plotly 散点 + 前沿连线，每个 backend 一种颜色
- 右侧 control panel：
  - dataset 选择 (liver PCA / synth100k / dim768)
  - top_k 滑块
  - 点击散点 → 滑块联动到对应 `ef_search` / `nprobe`
  - 实时 Top-K 预览（复用 SearchPage 的 Top-K 列表组件）

---

# 价值与对比

## 为什么这是「加分项 C3」

1. **业界标准**：ANN-Benchmarks (erikbern/ann-benchmarks) 是 ANN 算法评测事实标准，论文必备图
2. **覆盖 v1.0 改进**：adaptive-hnsw 的收益首次有了**量化曲线**对比
3. **可复现**：所有数据落表，`sweep_run_id` 可重复拉取与外部工具集成
4. **前端联动**：不只是静态图，而是交互式参数扫描 + Top-K 反查

## 与既有 §5.7 dim 扫描的关系

| 维度 | §5.7 dim 扫描 | §7 帕累托扫描（本次） |
| --- | --- | --- |
| 固定项 | backend 参数 (ef=64) | dim (=30) |
| 扫描项 | dim ∈ {10..768} | ef_search / nprobe |
| 主要回答 | 各 backend 在不同维度的横向对比 | 同 backend 内的精度-吞吐权衡 |
| 数据形态 | 折线图（每 backend 一条） | 散点 + 前沿（每 backend 一组） |

两者拼接 = 任意 (dataset, dim, backend, params) 的完整性能画像。

---

# 占位/待办 (M1-α → 回填)

- [ ] 等 `POST /sweep` 上线并跑完 5 backend × 平均 6 点 ≈ 30 点
- [ ] 用 regex 把 `benchmark_report.md` §7.3 中 `0.99XX` / `XXXXX` 替换为实数
- [ ] 用 Plotly 静态导出 3 张 PNG，放入 `docs/assets/benchmark/`
  - `pareto_pca30.png` (PCA 30D / 主图)
  - `pareto_synth100k.png` (synth N=100k / dim=50)
  - `pareto_dim768.png` (synth dim=768，配合 §5.7)
- [ ] 把本草稿要点合入 `docs/slides/answer_defense.md`（由主代理决定插入位置）
- [ ] 录一段 ≤30s 的「参数扫描 Tab」交互演示，链接到 `docs/video/`
