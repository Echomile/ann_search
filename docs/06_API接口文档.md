# 六、API 接口文档

> 本文是 **31 个 REST 接口** 的速查文档（含管理员 / 缓存 / 统计 / 进度等运维接口），便于团队对照与第三方集成。
>
> - **完整 OpenAPI 规范**：<http://localhost:8000/openapi.json>
> - **交互式 Swagger UI**：<http://localhost:8000/docs>
> - **ReDoc 风格文档**：<http://localhost:8000/redoc>
>
> 后端使用 FastAPI + Pydantic v2 自动生成 schema 与文档，所有 `summary` / `description` / 字段约束均由源码装饰器派生，保证文档与实现 100% 一致。

## 6.1 通用约定

### 6.1.1 基础信息

| 项 | 值 |
| --- | --- |
| API 前缀 | `/api/v1`（健康检查除外，位于 `/health`）|
| 鉴权方式 | `Authorization: Bearer <jwt>`（除注册 / 登录 / 健康检查外全部需要）|
| 请求格式 | JSON（默认）/ `multipart/form-data`（上传）/ `application/x-www-form-urlencoded`（登录）|
| 响应格式 | JSON，时间字段 ISO 8601 |
| 字符集 | UTF-8 |

### 6.1.2 HTTP 状态码约定

| 状态码 | 含义 | 触发场景 |
| --- | --- | --- |
| 200 | 成功 | 默认成功响应 |
| 201 | 已创建 | 注册、上传成功 |
| 202 | 已接受 | 异步任务入队（索引构建、评测）|
| 204 | 成功无内容 | 预留 |
| 400 | 请求错误 | 业务规则失败（如用户名重复、空文件）|
| 401 | 未认证 | 凭据缺失 / 错误 / 过期 |
| 403 | 越权 | 操作非自有资源 |
| 404 | 不存在 | 资源未找到 |
| 409 | 状态冲突 | 资源状态不允许当前操作（如索引未 ready）|
| 422 | 参数校验失败 | Pydantic 校验 / 维度不匹配 |
| 500 | 服务器错误 | 未处理的内部异常 |
| 503 | 依赖不可用 | ARQ / Redis 未就绪 |

### 6.1.3 错误响应统一格式

```json
{
  "detail": "用户名或密码错误"
}
```

422 Pydantic 校验失败时为 `detail` 数组：

```json
{
  "detail": [
    {
      "loc": ["body", "password"],
      "msg": "ensure this value has at least 6 characters",
      "type": "value_error.any_str.min_length"
    }
  ]
}
```

## 6.2 健康检查

### 6.2.1 `GET /health`

| 项 | 值 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/health` |
| 鉴权 | 否 |
| 描述 | 健康检查接口，用于探活与负载均衡。 |

**响应 200**：

```json
{ "status": "ok" }
```

## 6.3 认证模块（Auth）

源码：[`backend/app/api/v1/auth.py`](../backend/app/api/v1/auth.py)

### 6.3.1 `POST /api/v1/auth/register`

| 项 | 值 |
| --- | --- |
| `summary` | 用户注册 |
| 鉴权 | 否 |
| 描述 | 使用用户名与明文密码注册新账号；首位注册的用户自动获得 `admin` 角色。 |

**请求体（JSON, `UserCreate`）**：

| 字段 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `username` | string | 是 | 3 ≤ len ≤ 32 | 登录用户名 |
| `password` | string | 是 | 6 ≤ len ≤ 128 | 明文密码 |

**响应 201（`UserOut`）**：

```json
{
  "id": 1,
  "username": "demo",
  "role": "admin",
  "created_at": "2026-05-23T04:00:00Z"
}
```

**典型错误**：

- `400` 用户名已存在；
- `422` 字段长度不符合约束。

### 6.3.2 `POST /api/v1/auth/login`

| 项 | 值 |
| --- | --- |
| `summary` | 用户登录 |
| 鉴权 | 否 |
| 请求类型 | `application/x-www-form-urlencoded`（OAuth2 Password Flow）|

**请求字段**：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `username` | string | 是 | — |
| `password` | string | 是 | — |

**响应 200（`TokenOut`）**：

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "user": {
    "id": 1,
    "username": "demo",
    "role": "admin",
    "created_at": "2026-05-23T04:00:00Z"
  }
}
```

**典型错误**：`401` 用户名或密码错误（不暴露账号存在性）。

### 6.3.3 `GET /api/v1/auth/me`

| 项 | 值 |
| --- | --- |
| `summary` | 获取当前用户 |
| 鉴权 | 是 |

**请求头**：`Authorization: Bearer <jwt>`

**响应 200（`UserOut`）**：见 6.3.1。

**典型错误**：`401` 令牌缺失 / 无效 / 过期。

## 6.4 数据集模块（Datasets）

源码：[`backend/app/api/v1/datasets.py`](../backend/app/api/v1/datasets.py)

### 6.4.1 `POST /api/v1/datasets/upload`

| 项 | 值 |
| --- | --- |
| `summary` | 上传数据集 |
| 鉴权 | 是 |
| 请求类型 | `multipart/form-data` |
| 描述 | 通过 multipart 流式上传 `.h5ad`；服务端 8 MB 分块写盘；落盘后立即入队 ARQ 预处理任务。 |

**请求字段**：

| 字段 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `name` | string | 是 | 1 ≤ len ≤ 255 | 数据集名称 |
| `file` | file | 是 | `.h5ad`，建议 ≤ 2 GB | AnnData 文件 |

**响应 201（`DatasetUploadResponse`）**：

```json
{
  "dataset": {
    "id": 1,
    "owner_id": 1,
    "name": "liver",
    "status": "uploading",
    "cell_count": null,
    "vector_dim": null,
    "vector_source": null,
    "meta_columns": null,
    "created_at": "2026-05-23T04:01:00Z"
  },
  "task_id": "arq:job:abc123"
}
```

**典型错误**：`400` 文件为空；`500` 写盘失败。

### 6.4.2 `GET /api/v1/datasets`

| 项 | 值 |
| --- | --- |
| `summary` | 数据集列表 |
| 鉴权 | 是 |
| 描述 | 返回当前用户拥有的全部数据集，按 `created_at` 倒序。 |

**响应 200（`list[DatasetOut]`）**：

```json
[
  {
    "id": 1,
    "owner_id": 1,
    "name": "liver",
    "status": "ready",
    "cell_count": 69032,
    "vector_dim": 30,
    "vector_source": "X_pca",
    "meta_columns": ["cell_type", "disease", "tissue"],
    "created_at": "2026-05-23T04:01:00Z"
  }
]
```

### 6.4.3 `GET /api/v1/datasets/{dataset_id}`

| 项 | 值 |
| --- | --- |
| `summary` | 数据集详情 |
| 鉴权 | 是 |

**响应 200（`DatasetOut`）**：同 6.4.2 数组元素。

**典型错误**：`403` 非拥有者；`404` 不存在。

### 6.4.4 `DELETE /api/v1/datasets/{dataset_id}`

| 项 | 值 |
| --- | --- |
| `summary` | 删除数据集 |
| 鉴权 | 是 |
| 描述 | 级联删除 `index_records` 并清理磁盘文件与索引目录。 |

**响应 200（`DatasetDeleteResponse`）**：

```json
{ "deleted": true, "dataset_id": 1 }
```

### 6.4.5 `GET /api/v1/datasets/{dataset_id}/status`

| 项 | 值 |
| --- | --- |
| `summary` | 数据集状态 |
| 鉴权 | 是 |
| 描述 | 轻量接口，用于前端轮询预处理进度。 |

**响应 200（`DatasetStatus`）**：

```json
{
  "dataset_id": 1,
  "status": "ready",
  "cell_count": 69032,
  "vector_dim": 30,
  "vector_source": "X_pca",
  "meta_columns": ["cell_type", "disease"]
}
```

## 6.5 索引模块（Indexes）

源码：[`backend/app/api/v1/indexes.py`](../backend/app/api/v1/indexes.py)

### 6.5.1 `POST /api/v1/datasets/{dataset_id}/indexes`

| 项 | 值 |
| --- | --- |
| `summary` | 构建索引 |
| 鉴权 | 是 |
| 描述 | 按指定后端与参数为目标数据集构建 ANN 索引；写入 `IndexRecord(status=building)` 立即返回，构建在 ARQ Worker 异步执行。 |

**请求体（JSON, `IndexCreate`）**：

| 字段 | 类型 | 必填 | 取值 | 说明 |
| --- | --- | --- | --- | --- |
| `backend` | string | 是 | `hnswlib` / `faiss-hnsw` / `faiss-ivfpq` / `brute` / `adaptive-hnsw` | ANN 后端 |
| `metric` | string | 否 | `l2`（默认）/ `cosine` / `ip` | 距离度量 |
| `params` | object | 否 | 与后端相关 | 构建参数 |

**`params` 字段建议**：

- **hnswlib / faiss-hnsw / adaptive-hnsw**：`{"M": 16, "ef_construction": 200, "ef_search": 64}`；
- **faiss-ivfpq**：`{"nlist": 173, "m": 10, "nbits": 8, "nprobe": 16}`；
- **brute**：`{}`。

**响应 202（`IndexCreateResponse`）**：

```json
{
  "index": {
    "id": 5,
    "dataset_id": 1,
    "backend": "hnswlib",
    "metric": "l2",
    "params": { "M": 16, "ef_construction": 200, "ef_search": 64 },
    "index_path": null,
    "build_time_seconds": null,
    "memory_mb": null,
    "status": "building",
    "created_at": "2026-05-23T04:10:00Z"
  },
  "task_id": "arq:job:def456"
}
```

**典型错误**：`400` 数据集尚未 ready；`503` ARQ 不可用。

### 6.5.2 `GET /api/v1/datasets/{dataset_id}/indexes`

| 项 | 值 |
| --- | --- |
| `summary` | 数据集索引列表 |
| 鉴权 | 是 |
| 描述 | 按 `created_at` 倒序返回该数据集的全部索引记录。 |

**响应 200（`list[IndexRecordOut]`）**：见 6.5.3。

### 6.5.3 `GET /api/v1/indexes/{index_id}`

| 项 | 值 |
| --- | --- |
| `summary` | 索引详情 |
| 鉴权 | 是 |

**响应 200（`IndexRecordOut`）**：

```json
{
  "id": 5,
  "dataset_id": 1,
  "backend": "hnswlib",
  "metric": "l2",
  "params": { "M": 16, "ef_construction": 200, "ef_search": 64 },
  "index_path": "/indexes/1/5.bin",
  "build_time_seconds": 0.68,
  "memory_mb": 7.1,
  "status": "ready",
  "created_at": "2026-05-23T04:10:00Z"
}
```

### 6.5.4 `GET /api/v1/indexes/{index_id}/status`

| 项 | 值 |
| --- | --- |
| `summary` | 索引状态 |
| 鉴权 | 是 |
| 描述 | 轻量轮询接口，仅返回核心状态字段。 |

**响应 200（`IndexStatus`）**：

```json
{
  "id": 5,
  "status": "ready",
  "backend": "hnswlib",
  "build_time_seconds": 0.68,
  "memory_mb": 7.1
}
```

### 6.5.5 `DELETE /api/v1/indexes/{index_id}`

| 项 | 值 |
| --- | --- |
| `summary` | 删除索引 |
| 鉴权 | 是 |
| 描述 | 删除索引记录、磁盘文件，并从进程内 LRU 缓存中驱逐。 |

**响应 200（`Message`）**：

```json
{ "detail": "索引 5 已删除" }
```

## 6.6 检索模块（Search）

源码：[`backend/app/api/v1/search.py`](../backend/app/api/v1/search.py)

### 6.6.1 `POST /api/v1/search/by-id`

| 项 | 值 |
| --- | --- |
| `summary` | 按细胞 ID 检索 |
| 鉴权 | 是 |
| 描述 | 使用数据集内已有的 `cell_id` 作为查询点，返回 Top-K 相似细胞；查询点自身从结果中剔除；支持 metadata 过滤。 |

**请求体（JSON, `SearchByCellId`）**：

| 字段 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `dataset_id` | int | 是 | — | 数据集 ID |
| `cell_id` | string | 是 | — | 已存在的细胞编号 |
| `top_k` | int | 否 | 1 ≤ k ≤ 1000 | 默认 10 |
| `filters` | object | 否 | — | metadata 过滤 |
| `index_id` | int | 否 | — | 缺省取最新 ready |

**响应 200（`SearchResponse`）**：

```json
{
  "dataset_id": 1,
  "top_k": 10,
  "latency_ms": 0.52,
  "index_backend": "hnswlib",
  "metric": "l2",
  "total_candidates": 50,
  "hits": [
    {
      "rank": 1,
      "cell_id": "liver_0001",
      "distance": 0.0023,
      "meta": { "cell_type": "Hepatocyte", "disease": "Healthy" },
      "source_dataset_id": null
    }
  ]
}
```

**典型错误**：`404` cell_id 不存在；`409` 索引未 ready。

### 6.6.2 `POST /api/v1/search/by-vector`

| 项 | 值 |
| --- | --- |
| `summary` | 按向量检索 |
| 鉴权 | 是 |
| 描述 | 使用用户自定义向量作为查询点；默认 post-filter，先取 `top_k * 5` 候选再筛选。 |

**请求体（JSON, `SearchByVector`）**：

| 字段 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `dataset_id` | int | 是 | — | — |
| `vector` | float[] | 是 | 长度 = `vector_dim` | 查询向量 |
| `top_k` | int | 否 | 1 ≤ k ≤ 1000 | 默认 10 |
| `filters` | object | 否 | — | metadata 过滤 |
| `index_id` | int | 否 | — | 同上 |

**典型错误**：`422` 维度不匹配。

### 6.6.3 `POST /api/v1/search/multi-dataset`

| 项 | 值 |
| --- | --- |
| `summary` | 跨数据集联合检索 |
| 鉴权 | 是 |
| 描述 | 并发对多个数据集执行检索，按 min-max 归一化距离合并 Top-K，每条结果带 `source_dataset_id`。`cell_id` 与 `vector` 二选一。 |

**请求体（JSON, `MultiDatasetSearchRequest`）**：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `dataset_ids` | int[] | 是 | 至少 1 个 |
| `index_ids` | int[] \| null | 否 | 长度需与 `dataset_ids` 一致；缺省每个数据集取最新 ready |
| `cell_id` | string \| null | 否 | 与 `source_dataset_id` 联合 |
| `source_dataset_id` | int \| null | 否 | 缺省取 `dataset_ids[0]` |
| `vector` | float[] \| null | 否 | 与 `cell_id` 二选一 |
| `top_k` | int | 否 | 默认 10 |
| `filters` | object | 否 | — |

**响应 200（`SearchResponse`，`dataset_id=null`，`index_backend="multi"`）**：每条 `hit` 的 `source_dataset_id` 非空。

**典型错误**：`422` 两个查询源都为空 / `index_ids` 长度不匹配；`404` source `cell_id` 不在指定数据集中。

## 6.7 评测模块（Evaluation）

源码：[`backend/app/api/v1/evaluation.py`](../backend/app/api/v1/evaluation.py)

### 6.7.1 `POST /api/v1/evaluation/run`

| 项 | 值 |
| --- | --- |
| `summary` | 发起索引基准评测 |
| 鉴权 | 是 |
| 描述 | 对指定索引执行 Recall、QPS、延迟分位评测；ARQ 可用时入队异步执行，否则降级前台同步执行。 |

**请求体（JSON, `BenchmarkRequest`）**：

| 字段 | 类型 | 必填 | 约束 | 默认 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `index_id` | int | 是 | — | — | 待评测索引 |
| `num_queries` | int | 否 | 1 ≤ x ≤ 10000 | 100 | 评测查询数 |
| `top_k_list` | int[] | 否 | — | `[10, 100]` | Recall 评测 K 值 |
| `concurrency_list` | int[] | 否 | — | `[1, 4, 8, 16]` | 并发档位 |

**响应 202（`BenchmarkTaskHandle`）**：

```json
{
  "task_id": "arq:job:eval-abc",
  "index_id": 5,
  "status": "queued"
}
```

### 6.7.2 `GET /api/v1/evaluation/{index_id}/latest`

| 项 | 值 |
| --- | --- |
| `summary` | 索引最近一次评测结果 |
| 鉴权 | 是 |

**响应 200（`BenchmarkResult`）**：

```json
{
  "index_id": 5,
  "dataset_id": 1,
  "backend": "hnswlib",
  "metric": "l2",
  "build_time_seconds": 0.224,
  "memory_mb": 7.1,
  "num_queries": 500,
  "recalls": { "10": 0.9996, "100": 0.9976 },
  "latencies": [
    {
      "concurrency": 1,
      "p50_ms": 0.016,
      "p95_ms": 0.020,
      "p99_ms": 0.021,
      "mean_ms": 0.016,
      "qps": 63158.2,
      "total_queries": 500
    }
  ],
  "finished_at": "2026-05-23T04:30:00Z"
}
```

**典型错误**：`404` 尚无评测结果。

### 6.7.3 `GET /api/v1/evaluation/results`

| 项 | 值 |
| --- | --- |
| `summary` | 评测结果列表 |
| 鉴权 | 是 |
| 查询参数 | `dataset_id` (int, 可选)，按数据集过滤 |
| 描述 | 返回历史评测结果摘要列表，按索引 ID 聚合。 |

**响应 200**：

```json
[
  {
    "index_id": 5,
    "dataset_id": 1,
    "backend": "hnswlib",
    "recalls": { "10": 0.9996, "100": 0.9976 },
    "finished_at": "2026-05-23T04:30:00Z"
  }
]
```

## 6.8 RAG 模块（加分）

源码：[`backend/app/api/v1/rag.py`](../backend/app/api/v1/rag.py)

### 6.8.1 `POST /api/v1/rag/query`

| 项 | 值 |
| --- | --- |
| `summary` | 自然语言检索 |
| 鉴权 | 是 |
| 描述 | 用自然语言提问，LLM 解析为结构化检索参数（`cell_id` 或 metadata 过滤条件），调用 ANN 检索后再由 LLM 生成自然语言回答。`LLM_PROVIDER` 支持 `mock` / `dashscope`（通义千问）/ `openai`（OpenAI 兼容端点）/ `anthropic`（Claude Opus，例如 `claude-opus-4-20250514`），其中 `mock` 使用关键词规则与模板化总结，无需任何外部 API Key；真实 provider 在 SDK 缺失或调用失败时自动回退 Mock 保证可用性。 |

**LLM provider 配置（`.env`）**：

| 变量 | 适用 provider | 说明 |
| --- | --- | --- |
| `LLM_PROVIDER` | 全部 | 取值 `mock` / `dashscope` / `openai` / `anthropic`，默认 `mock` |
| `LLM_MODEL` | 全部 | 例如 `qwen-plus` / `gpt-4o-mini` / `claude-opus-4-20250514` |
| `LLM_API_KEY` | dashscope / openai / anthropic | 通用 API Key |
| `ANTHROPIC_API_KEY` | anthropic | 可选，配置后覆盖 `LLM_API_KEY` 仅用于 anthropic |
| `LLM_BASE_URL` | openai | 仅 OpenAI 兼容 endpoint 时使用 |

**请求体（JSON, `RagQueryRequest`）**：

| 字段 | 类型 | 必填 | 约束 | 说明 |
| --- | --- | --- | --- | --- |
| `dataset_id` | int | 是 | — | 目标数据集 |
| `index_id` | int | 否 | — | 缺省取最新 ready |
| `query` | string | 是 | 非空 | 自然语言提问 |
| `top_k` | int | 否 | 1 ≤ k ≤ 100 | 默认 10 |

**响应 200（`RagResponse`）**：

```json
{
  "parsed": {
    "cell_id": null,
    "filters": { "cell_type": "Hepatocyte" },
    "top_k": 10,
    "intent": "查找与肝细胞类似的细胞 Top-10"
  },
  "hits": [
    {
      "rank": 1,
      "cell_id": "liver_0001",
      "distance": 0.0023,
      "meta": { "cell_type": "Hepatocyte" }
    }
  ],
  "answer": "已为您找到 10 个 cell_type 为 Hepatocyte 的相似细胞...",
  "query_time_ms": 18.5
}
```

## 6.9 接口汇总表（速查 21 个）

| # | 模块 | 方法 | 路径 | summary | 鉴权 |
| --- | --- | --- | --- | --- | --- |
| 1 | health | GET | `/health` | 健康检查 | × |
| 2 | auth | POST | `/api/v1/auth/register` | 用户注册 | × |
| 3 | auth | POST | `/api/v1/auth/login` | 用户登录 | × |
| 4 | auth | GET | `/api/v1/auth/me` | 获取当前用户 | ✓ |
| 5 | datasets | POST | `/api/v1/datasets/upload` | 上传数据集 | ✓ |
| 6 | datasets | GET | `/api/v1/datasets` | 数据集列表 | ✓ |
| 7 | datasets | GET | `/api/v1/datasets/{dataset_id}` | 数据集详情 | ✓ |
| 8 | datasets | DELETE | `/api/v1/datasets/{dataset_id}` | 删除数据集 | ✓ |
| 9 | datasets | GET | `/api/v1/datasets/{dataset_id}/status` | 数据集状态 | ✓ |
| 10 | indexes | POST | `/api/v1/datasets/{dataset_id}/indexes` | 构建索引 | ✓ |
| 11 | indexes | GET | `/api/v1/datasets/{dataset_id}/indexes` | 数据集索引列表 | ✓ |
| 12 | indexes | GET | `/api/v1/indexes/{index_id}` | 索引详情 | ✓ |
| 13 | indexes | GET | `/api/v1/indexes/{index_id}/status` | 索引状态 | ✓ |
| 14 | indexes | DELETE | `/api/v1/indexes/{index_id}` | 删除索引 | ✓ |
| 15 | search | POST | `/api/v1/search/by-id` | 按细胞 ID 检索 | ✓ |
| 16 | search | POST | `/api/v1/search/by-vector` | 按向量检索 | ✓ |
| 17 | search | POST | `/api/v1/search/multi-dataset` | 跨数据集联合检索 | ✓ |
| 18 | evaluation | POST | `/api/v1/evaluation/run` | 发起索引基准评测 | ✓ |
| 19 | evaluation | GET | `/api/v1/evaluation/{index_id}/latest` | 索引最近一次评测结果 | ✓ |
| 20 | evaluation | GET | `/api/v1/evaluation/results` | 评测结果列表 | ✓ |
| 21 | rag | POST | `/api/v1/rag/query` | 自然语言检索（加分项）| ✓ |

## 6.10 cURL 示例

### 6.10.1 注册 + 登录 + 当前用户

```bash
# 注册
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"username":"demo","password":"demo1234"}'

# 登录
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'username=demo&password=demo1234' | jq -r .access_token)

# me
curl http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer $TOKEN"
```

### 6.10.2 上传数据集

```bash
curl -X POST http://localhost:8000/api/v1/datasets/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F 'name=liver' \
  -F 'file=@/path/to/liver.h5ad'
```

### 6.10.3 构建 HNSWLIB 索引并轮询状态

```bash
# 构建（数据集 ID = 1）
curl -X POST http://localhost:8000/api/v1/datasets/1/indexes \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "backend": "hnswlib",
    "metric": "l2",
    "params": {"M": 16, "ef_construction": 200, "ef_search": 64}
  }'

# 轮询（索引 ID = 5）
watch -n 3 "curl -s http://localhost:8000/api/v1/indexes/5/status \
  -H 'Authorization: Bearer $TOKEN' | jq"
```

### 6.10.4 按 cell_id 检索

```bash
curl -X POST http://localhost:8000/api/v1/search/by-id \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "dataset_id": 1,
    "cell_id": "liver_0001",
    "top_k": 10,
    "filters": {"cell_type": "Hepatocyte"}
  }'
```

### 6.10.5 跨数据集联合检索

```bash
curl -X POST http://localhost:8000/api/v1/search/multi-dataset \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "dataset_ids": [1, 2],
    "cell_id": "liver_0001",
    "source_dataset_id": 1,
    "top_k": 10
  }'
```

### 6.10.6 发起评测

```bash
curl -X POST http://localhost:8000/api/v1/evaluation/run \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "index_id": 5,
    "num_queries": 500,
    "top_k_list": [10, 100],
    "concurrency_list": [1, 4, 8]
  }'
```

### 6.10.7 RAG 自然语言查询

```bash
curl -X POST http://localhost:8000/api/v1/rag/query \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "dataset_id": 1,
    "query": "找出与肝细胞相似的细胞 Top-10",
    "top_k": 10
  }'
```

## 6.11 字段速查

| Schema | 关键字段 |
| --- | --- |
| `UserCreate` | `username` (3-32), `password` (6-128) |
| `UserOut` | `id`, `username`, `role`, `created_at` |
| `TokenOut` | `access_token`, `token_type=bearer`, `user` |
| `DatasetOut` | `id`, `owner_id`, `name`, `status`, `cell_count`, `vector_dim`, `vector_source`, `meta_columns`, `created_at` |
| `DatasetStatus` | `dataset_id`, `status`, `cell_count`, `vector_dim`, `vector_source`, `meta_columns` |
| `IndexCreate` | `backend`, `metric=l2`, `params={}` |
| `IndexRecordOut` | `id`, `dataset_id`, `backend`, `metric`, `params`, `index_path`, `build_time_seconds`, `memory_mb`, `status`, `created_at` |
| `IndexStatus` | `id`, `status`, `backend`, `build_time_seconds`, `memory_mb` |
| `SearchByCellId` | `dataset_id`, `cell_id`, `top_k=10`, `filters`, `index_id` |
| `SearchByVector` | `dataset_id`, `vector`, `top_k=10`, `filters`, `index_id` |
| `MultiDatasetSearchRequest` | `dataset_ids`, `index_ids`, `cell_id` \| `vector`, `source_dataset_id`, `top_k=10`, `filters` |
| `SearchResponse` | `dataset_id`, `top_k`, `latency_ms`, `index_backend`, `metric`, `total_candidates`, `hits[]` |
| `SearchHit` | `rank`, `cell_id`, `distance`, `meta`, `source_dataset_id` |
| `BenchmarkRequest` | `index_id`, `num_queries=100`, `top_k_list=[10,100]`, `concurrency_list=[1,4,8,16]` |
| `BenchmarkResult` | `index_id`, `dataset_id`, `backend`, `metric`, `build_time_seconds`, `memory_mb`, `num_queries`, `recalls`, `latencies[]`, `finished_at` |
| `RagQueryRequest` | `dataset_id`, `index_id`, `query`, `top_k=10` |
| `RagResponse` | `parsed`, `hits[]`, `answer`, `query_time_ms` |
| `ParsedQuery` | `cell_id`, `filters`, `top_k=10`, `intent` |

> 如需机器可读的完整 schema，请直接拉取 `/openapi.json` 并配合 `openapi-typescript` / `openapi-python-client` 生成客户端代码；本仓库前端 `frontend/src/api/*.ts` 即采用该方式生成 + 手工微调。

## 6.10 v1.1 新增接口（管理员 / 缓存 / 进度 / 可视化）

### 6.10.1 数据集进阶

| 方法 | 路径 | 摘要 | 备注 |
| --- | --- | --- | --- |
| `PATCH` | `/datasets/{dataset_id}` | 重命名数据集 | body `{name}`；同名 409；非拥有者 403 |
| `DELETE` | `/datasets/orphan` | 批量清理失败 / 孤儿数据集 | 当前用户名下 `status=failed` 或缺失向量文件的全部清理，返回 `{deleted_ids, count}` |
| `GET` | `/datasets/{dataset_id}/upload-progress` | 上传 / 写盘进度 | 返回 `{status, bytes_received, total_bytes, percent}`；预处理阶段 `total_bytes=null` 用 indeterminate spinner |
| `GET` | `/datasets/{dataset_id}/umap` | 真实 UMAP 2D 坐标 | 返回 `{has_umap, coords: number[][], cell_ids, sampled, total_cells}`；N > 5 万自动下采样；文件缺失返回 `has_umap=false` + 200 |

curl 示例：

```bash
# 重命名
curl -X PATCH "$API/datasets/3" -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' -d '{"name":"liver_v2"}'

# 清理失败数据集
curl -X DELETE "$API/datasets/orphan" -H "Authorization: Bearer $TOKEN"

# 实时进度（前端 500ms 轮询）
curl "$API/datasets/3/upload-progress" -H "Authorization: Bearer $TOKEN"

# UMAP 散点
curl "$API/datasets/3/umap" -H "Authorization: Bearer $TOKEN" | jq '.coords | length'
```

### 6.10.2 索引进阶

| 方法 | 路径 | 摘要 | 备注 |
| --- | --- | --- | --- |
| `GET` | `/indexes/cache/stats` | 两层缓存命中率 | 聚合 IndexCache（进程 LRU）+ SearchCache（Redis）返回字段，详见下方示例 |
| `GET` | `/indexes/{index_id}/latest-benchmark` | 索引视角读最近评测 | 与 `/evaluation/{index_id}/latest` 等价但走 indexes 路由；无评测返回 `{has_benchmark: false}` + 200 |

`GET /indexes/cache/stats` 返回示例（F2 part2-b 起合并两层缓存）：

```json
{
  "capacity": 4,
  "size": 2,
  "hits": 12,
  "misses": 3,
  "loads": 3,
  "evictions": 0,
  "hit_ratio": 0.8,
  "cached_index_ids": [2, 1],
  "search_cache_hits": 8,
  "search_cache_misses": 5,
  "search_cache_errors": 0,
  "search_cache_hit_ratio": 0.6154
}
```

字段分组说明：

- **前 8 个字段**属于 **IndexCache**（`backend/app/services/ann/cache.py`，进程内 LRU 常驻 ANN 索引）：`capacity` 为容量上限；`size` 为当前驻留数；`hits / misses` 为加载是否命中缓存；`loads` 为累计加载次数；`evictions` 为 LRU 淘汰次数；`hit_ratio` 为四位小数命中率；`cached_index_ids` 为当前驻留索引 ID 列表（按 LRU 顺序）。
- **后 4 个字段**属于 **SearchCache**（`backend/app/services/search_cache.py`，Redis 检索结果缓存，key = SHA256(`v1|index_id|top_k|query|filters`)，TTL 默认 300s）：`search_cache_hits / misses` 来自 `cached_or_compute` 的 Redis GET 命中/未命中；`search_cache_errors` 累计 Redis 异常计数（异常不影响主链路）；`search_cache_hit_ratio` 为四位小数命中率。Redis 不可用时各字段保持 0 并不影响检索。

### 6.10.3 检索日志统计

| 方法 | 路径 | 摘要 | 备注 |
| --- | --- | --- | --- |
| `GET` | `/stats/search?dataset_id=` | 检索日志聚合 | 总查询数 / 平均 / P95 延迟 + 按数据集聚合 + 最近 24h 每小时桶；`dataset_id` 可选 |

返回示例（节选）：

```json
{
  "total_queries": 5,
  "overall_avg_latency_ms": 42.0,
  "overall_p95_latency_ms": 89.99,
  "by_dataset": [
    {"dataset_id": 1, "dataset_name": "liver_demo", "total_queries": 4, "avg_latency_ms": 40.0, "p95_latency_ms": 89.49}
  ],
  "hourly_24h": [
    {"hour_iso": "2026-05-23T01:00:00+00:00", "queries": 0, "avg_latency_ms": 0.0},
    "... 共 24 项 ..."
  ]
}
```

### 6.10.4 管理员（admin）

全部接口需 `current_user.role == "admin"`，否则 403。

| 方法 | 路径 | 摘要 | 备注 |
| --- | --- | --- | --- |
| `GET` | `/admin/users` | 列出全部用户 | 返回 `UserOut[]`（不含密码） |
| `PATCH` | `/admin/users/{user_id}` | 修改角色 | body `{role: "admin"\|"user"}`；改自己 403 |
| `DELETE` | `/admin/users/{user_id}` | 删除用户 | 级联清理数据集 / 索引 / 检索日志 + 磁盘文件；删自己 403 |
| `POST` | `/admin/users/{user_id}/reset-password` | 重置密码 | 服务端生成 12 字符随机密码，bcrypt 入库，**仅本次返回**明文 `temp_password` |

curl 示例：

```bash
# 提升为管理员
curl -X PATCH "$API/admin/users/2" -H "Authorization: Bearer $ADMIN" \
     -H 'Content-Type: application/json' -d '{"role":"admin"}'

# 重置密码（一次性）
curl -X POST "$API/admin/users/2/reset-password" -H "Authorization: Bearer $ADMIN"
# -> {"user_id":2,"temp_password":"sjpmieh1yXAq"}
```

## 6.11 接口总览（31 个）

| 模块 | 数量 | 路径前缀 |
| --- | ---: | --- |
| 健康检查 | 1 | `/health` |
| 用户认证 | 3 | `/auth/*` |
| 管理员（admin） | 4 | `/admin/users*` |
| 数据集（含 v1.1 新增 4 个） | 9 | `/datasets/*` |
| 索引（含 v1.1 新增 2 个） | 7 | `/datasets/{id}/indexes`, `/indexes/*` |
| 检索 | 3 | `/search/*` |
| 评测 | 3 | `/evaluation/*` |
| 检索日志统计 | 1 | `/stats/search` |
| RAG | 1 | `/rag/query` |
| **合计** | **31** | — |

