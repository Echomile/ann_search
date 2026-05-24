# 项目进展总结（截至 v1.1.0）

> 单细胞高维向量近似最近邻 (ANN) 检索系统 · 软件工程大作业
> 报告日期：2026-05-24 · 当前 tag：**v1.1.0** · 分支：`develop`

本文档汇总 **v1.0.0 → v1.1.0** 期间的全部交付增量，作为答辩展示与里程碑复盘的单一信息源。
按"功能 / 性能 / 体验 / 测试 / 文档 / 演示"六个维度组织，数据均与 [`CHANGELOG.md`](../CHANGELOG.md) / [`submission/MANIFEST.md`](../submission/MANIFEST.md) 对齐。

---

## 一、里程碑总览

| 版本 | 日期 | 主题 | 关键产出 |
| --- | --- | --- | --- |
| **v1.0.0** | 2026-05-23 | 课程要求首版 + 三项扩展功能 | 21 个 REST 接口 / 21 张 PPT / 9 张验收截图 / 5'54" 演示视频 / 41 commit |
| **v1.1.0** | 2026-05-24 | feat + perf + polish 平衡升级 | **31+ 接口 / 25 张 PPT / 14 张截图 / 7'42" 演示视频 / 6 张架构图 / 34 commit** |

> v1.0.0 总 commit 41 个；v1.1.0 增量 commit **34** 个（`git log v1.0.0..v1.1.0 --oneline | wc -l`）。

## 二、v1.1.0 增量分类统计

按 Conventional Commits 类别汇总 v1.0.0..v1.1.0 区间的 34 个 commit：

| 类别 | 数量 | 代表 commit |
| --- | ---: | --- |
| `feat(search)` | 5 | F1 batch / F6 SSE / F7 ensemble |
| `feat(cache)` | 2 | F2 Redis 缓存 + IndexCache stats |
| `feat(perf)` | 2 | F3 mmap + F4 启动预热 / F5 fp16 |
| `feat(ui)` | 2 | B2/B3 移动响应式 + skeleton / SSE+ensemble Tab |
| `feat(rag)` | 1 | F8 Anthropic Claude |
| `feat(indexes/datasets/demo/frontend)` | 4 | C1/C3/C4 + A2 视频 v2 + IndexDetailPage |
| `perf` | 2 | P2 numba 3.15× / P3 N+1 / P4 brotli |
| `test(e2e)` | 3 | D2 admin / upload / stats / rag + 截图扩展 |
| `test(frontend)` | 2 | D1+D4 vitest 拓展 |
| `docs` | 6 | 06 API / A1 架构图 / A3 PPT / A4 FAQ / P1 100k / v1.1.0 release notes |
| `chore` | 2 | 全仓 format / stats router 清理 |
| `build` | 2 | E2 multi-arch / uv.lock 同步 |
| `ci` | 1 | D3 vitest CI step |
| **合计** | **34** | |

## 三、维度对照表（v1.0.0 vs v1.1.0）

| 维度 | v1.0.0 | v1.1.0 | 增量 / 说明 |
| --- | ---: | ---: | --- |
| 后端 pytest 用例 | 47 | **76** | +29（F2 缓存 / F8 Anthropic / IndexCache / AdaptiveHNSW 等） |
| 前端 vitest 用例 | 23 | **42** | +19（utils + hooks + stores） |
| E2E Playwright 流程 | 1 | **5** | +4（admin / upload-progress / stats / rag） |
| REST 接口数 | 21 | **31+** | +10（F1/F2/F6/F7 + C1/C3/C4 等） |
| 答辩 PPT 张数 | 21 | **25** | +4 张 v1.1 演进专题页（A3） |
| 真实数据截图 | 9 | **14** | +5（admin / SearchLog Dashboard / IndexDetail / 100k / multi） |
| 演示视频时长 | 5'54" | **7'42"** | +4 段（admin / Dashboard / IndexDetail / 新 Tab，共 15 个 step） |
| 架构图 | 1（mermaid 嵌入） | **6**（PNG/SVG） | A1 一键导出脚本 + 6 张专业图 |
| BruteBackend 检索速度 | 1.0× | **3.15×** | P2 numba JIT |
| 前端 plotly 包体 | 4.47 MB | **1.07 MB** | B1 basic-dist 替换全量 |

## 四、八项扩展功能（F1~F8）

| 编号 | 功能 | 接口 / 模块 | 收益 / 状态 |
| :---: | --- | --- | --- |
| **F1** | 批量检索 + 缓存复用 | `POST /api/v1/search/batch` | 单次最多 64 查询，命中缓存零计算 |
| **F2** | Redis 检索结果缓存 | `search/cache.py` + `GET /search/cache/stats` | 全链路缓存，命中率可观测 |
| **F3** | 索引 mmap 加载 | `IndexCache.load_index` | 大索引冷启动内存减半 |
| **F4** | 启动预热 IndexCache | `worker.on_startup` | 消除首查冷启动 50~200 ms |
| **F5** | 向量 float16 落盘 | `preprocess.py` | 磁盘体积减半，Recall@10 仅降 0.4% |
| **F6** | SSE 流式检索 | `POST /api/v1/search/stream` | 浏览器逐条吐结果 |
| **F7** | ensemble 多后端融合 | `POST /api/v1/search/ensemble` | z-score 归一化 + 加权融合 |
| **F8** | Anthropic Claude LLM | `LLM_PROVIDER=anthropic` | RAG 第 4 个 provider |

## 五、四项性能优化（P1~P4）

- **P1** N=100k 大规模真机基准实测，详见 [`benchmark_report.md`](benchmark_report.md) 5.6 节
- **P2** `numba` 加速 BruteBackend 暴力检索 — **3.15× 提速**（conc=1）
- **P3** SQLAlchemy `selectinload` 预加载消除数据集列表 N+1 查询
- **P4** brotli / gzip 响应压缩中间件 — 大 JSON 响应体减少 **70%+**

## 六、展示资源同步状态

| 资源 | 文件 | 现状 |
| --- | --- | --- |
| 演示视频 v2 | [`docs/video/demo_final.mp4`](video/demo_final.mp4) | **7'42"** · 1440×900 · H.264 · 15 段中文配音 |
| 视频首帧封面 | [`docs/assets/demo_cover.png`](assets/demo_cover.png) | 已重新提取 |
| 答辩 PPT | [`docs/slides/answer_defense.pdf`](slides/answer_defense.pdf) · `.pptx` | **25 张**（含 v1.1 演进 4 张） |
| 讲稿 | [`docs/slides/speaker_notes.md`](slides/speaker_notes.md) | 25 页对应中文口语稿 |
| E2E 截图 | [`docs/e2e_screenshots/`](e2e_screenshots/) | **14 张**（已合并 admin / Dashboard / IndexDetail） |
| 架构图 | [`docs/assets/architecture/`](assets/architecture/) | 6 张 PNG/SVG + mermaid 源 |
| 性能基准报告 | [`docs/benchmark_report.md`](benchmark_report.md) | 含 N=30k & N=100k 双规模 |
| API 接口文档 | [`docs/06_API接口文档.md`](06_API接口文档.md) | **31+** 接口完整 schema |
| 更新日志 | [`CHANGELOG.md`](../CHANGELOG.md) | v1.0.0 + v1.1.0 双里程碑 |
| 提交清单 | [`submission/MANIFEST.md`](../submission/MANIFEST.md) | 九大章节索引全部交付物 |

## 七、当前 Git 状态

```bash
git tag -l        # → v1.0.0  v1.1.0
git log v1.0.0..v1.1.0 --oneline | wc -l  # → 34
```

主分支：`develop`（生产基线 = v1.1.0）。所有 commit 均经 pre-commit hook 校验，Conventional Commits 规范。

## 八、答辩演练建议

按答辩 PPT 顺序与视频片段对应关系（共 25 张 / 7'42"）：

1. 第 1-3 张 ↔ 视频 0:00-0:30：项目背景 / 技术栈 / 系统架构
2. 第 4-8 张 ↔ 视频 0:30-2:30：数据集 / 索引 / 检索三大业务模块
3. 第 9-12 张 ↔ 视频 2:30-4:00：可视化 / 评测 / RAG 扩展功能
4. 第 13-16 张 ↔ 视频 4:00-5:30：v1.0 三项扩展功能（多数据集 / Adaptive HNSW / RAG）
5. 第 17-20 张 ↔ 视频 5:30-6:30：交付清单 / CI / 文档
6. 第 21-24 张 ↔ 视频 6:30-7:30：**v1.1 演进 4 张专题页**（性能 / 缓存 / 算法加速 / API 扩展）
7. 第 25 张 ↔ 视频 7:30-7:42：总结

---

> 本文件作为单一信息源，与 README / CHANGELOG / MANIFEST 数据完全对齐。
> 任何展示材料 (PPT / 视频 / 截图 / 文档) 的调整必须同步刷新本表。
