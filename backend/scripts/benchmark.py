"""ANN 后端性能基准测试脚本。

独立可运行的基准工具，对 ``brute / hnswlib / faiss-hnsw / faiss-ivfpq /
adaptive-hnsw`` 五个后端做横向对比，覆盖：

- 索引构建耗时与内存占用估计
- 不同 ``top_k`` 下相对 ``brute`` 的 ``Recall@k``
- 不同并发度下的单次延迟分位（``p50 / p95 / p99``）与吞吐 ``QPS``

数据源支持合成随机向量或 ``liver.h5ad`` 的 ``obsm['X_pca']``。
跑完后会同时输出 JSON 结果文件与 Markdown 实验报告。

典型用法::

    cd backend && uv run python scripts/benchmark.py --n 10000 --queries 200
    cd backend && uv run python scripts/benchmark.py \
        --n 5000 --queries 100 --backends brute,hnswlib,adaptive-hnsw
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.ann.factory import create_backend  # noqa: E402


def _pick_pq_m(dim: int) -> int:
    """为 IVF-PQ 挑选可整除 ``dim`` 的子量化器数 ``m``。

    Args:
        dim: 向量维度。

    Returns:
        int: 最优先取较大的可整除值，最少返回 ``1``。
    """
    for candidate in (16, 10, 8, 5, 4, 2, 1):
        if dim % candidate == 0:
            return candidate
    return 1


def _pick_pq_nlist(n_base: int) -> int:
    """根据底库规模启发式选择 ``nlist``。

    Args:
        n_base: 底库向量条数。

    Returns:
        int: 取 ``sqrt(n)`` 附近的整数，范围 ``[8, 4096]``。
    """
    nlist = int(max(8, min(4096, n_base**0.5)))
    return max(nlist, 8)


def _pkg_version(pkg: str) -> str:
    """读取已安装包的版本，失败返回 ``n/a``。"""
    try:
        from importlib.metadata import version

        return version(pkg)
    except Exception:
        return "n/a"


def collect_env_info() -> dict[str, Any]:
    """收集运行环境信息用于报告。"""
    try:
        import faiss

        faiss_version = getattr(faiss, "__version__", None) or _pkg_version("faiss-cpu")
    except Exception:
        faiss_version = "n/a"
    try:
        import hnswlib  # noqa: F401

        hnswlib_version = _pkg_version("hnswlib")
    except Exception:
        hnswlib_version = "n/a"
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "numpy": np.__version__,
        "faiss": faiss_version,
        "hnswlib": hnswlib_version,
    }


def load_vectors(args: argparse.Namespace) -> tuple[np.ndarray, np.ndarray, str]:
    """根据参数加载或生成向量。

    Args:
        args: 命令行参数。

    Returns:
        tuple[np.ndarray, np.ndarray, str]: ``(base, queries, source_desc)``。

    Raises:
        FileNotFoundError: ``--use-liver`` 指定但找不到 ``liver.h5ad``。
    """
    if args.use_liver:
        liver_path = Path(args.liver_path or (PROJECT_ROOT / "liver.h5ad"))
        if not liver_path.exists():
            raise FileNotFoundError(f"未找到 liver.h5ad: {liver_path}")
        import anndata

        adata = anndata.read_h5ad(liver_path, backed="r")
        x_pca = np.asarray(adata.obsm["X_pca"], dtype=np.float32)
        total = x_pca.shape[0]
        needed = args.n + args.queries
        if needed > total:
            raise ValueError(f"liver.h5ad 总向量数 {total} 不足，需要 {needed}")
        rng = np.random.default_rng(args.seed)
        perm = rng.permutation(total)[:needed]
        chunk = np.ascontiguousarray(x_pca[perm])
        base, queries = chunk[: args.n], chunk[args.n : needed]
        return base, queries, f"liver.h5ad obsm['X_pca'] (dim={chunk.shape[1]})"

    rng = np.random.default_rng(args.seed)
    base = rng.standard_normal((args.n, args.dim)).astype(np.float32)
    queries = rng.standard_normal((args.queries, args.dim)).astype(np.float32)
    return base, queries, f"合成 standard_normal (dim={args.dim})"


def percentile(values: list[float], q: float) -> float:
    """计算指定分位数的轻量实现。"""
    if not values:
        return 0.0
    return float(np.percentile(values, q))


def compute_recall(pred: np.ndarray, truth: np.ndarray, k: int) -> float:
    """计算 ``Recall@k``。

    Args:
        pred: 预测的近邻 id，形状 ``(M, k)``。
        truth: 暴力检索的 ground truth，形状 ``(M, k_gt)``，``k_gt >= k``。
        k: 评估的 top-k。

    Returns:
        float: 命中率，``[0, 1]``。
    """
    m = pred.shape[0]
    if m == 0:
        return 0.0
    truth_set = [set(truth[i, :k].tolist()) for i in range(m)]
    hits = sum(len(set(pred[i, :k].tolist()) & truth_set[i]) for i in range(m))
    return float(hits) / float(m * k)


def run_latency(
    backend: Any,
    queries: np.ndarray,
    top_k: int,
    concurrency: int,
) -> dict[str, float]:
    """在指定并发度下测量延迟与吞吐。

    Args:
        backend: 已经构建好索引的后端实例。
        queries: 查询向量批次，形状 ``(M, D)``。
        top_k: 单次查询取 top-k。
        concurrency: 并发线程数。

    Returns:
        dict[str, float]: ``p50_ms / p95_ms / p99_ms / mean_ms / qps``。
    """
    def task(q: np.ndarray) -> float:
        t0 = time.perf_counter()
        backend.search(q[None, :], top_k)
        return (time.perf_counter() - t0) * 1000.0

    wall_start = time.perf_counter()
    if concurrency <= 1:
        latencies = [task(q) for q in queries]
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            latencies = list(pool.map(task, queries))
    wall = time.perf_counter() - wall_start

    return {
        "p50_ms": percentile(latencies, 50),
        "p95_ms": percentile(latencies, 95),
        "p99_ms": percentile(latencies, 99),
        "mean_ms": float(statistics.fmean(latencies)) if latencies else 0.0,
        "qps": float(len(queries) / wall) if wall > 0 else 0.0,
        "wall_seconds": float(wall),
    }


def build_backend(
    name: str,
    base: np.ndarray,
    metric: str,
) -> tuple[Any, dict[str, Any]]:
    """根据后端名称创建实例并完成构建。

    Args:
        name: 后端名称。
        base: 底库向量。
        metric: 距离度量。

    Returns:
        tuple[Any, dict[str, Any]]: ``(backend, build_params)``。
    """
    n, dim = base.shape
    backend = create_backend(name, dim=dim, metric=metric)

    if name == "faiss-ivfpq":
        params = {
            "nlist": _pick_pq_nlist(n),
            "m": _pick_pq_m(dim),
            "nbits": 8,
            "nprobe": 16,
        }
    elif name in {"hnswlib", "faiss-hnsw"}:
        params = {"M": 16, "ef_construction": 200, "ef_search": 64}
    elif name == "adaptive-hnsw":
        params = {"M": 16, "ef_construction": 200}
    else:
        params = {}

    backend.build(base, **params)
    return backend, params


def measure_backend(
    name: str,
    base: np.ndarray,
    queries: np.ndarray,
    top_k_list: list[int],
    concurrency_list: list[int],
    metric: str,
    ground_truth: dict[int, np.ndarray],
    tmp_dir: Path,
) -> dict[str, Any]:
    """对单个后端进行完整基准测试。

    Args:
        name: 后端名称。
        base: 底库向量。
        queries: 查询向量。
        top_k_list: 待评估的 ``top_k`` 列表。
        concurrency_list: 待评估的并发度列表。
        metric: 距离度量。
        ground_truth: 来自 brute 的 ``{k: indices}`` 字典；若 brute 未跑，
            传入空字典则跳过 Recall 评估。
        tmp_dir: 用于 save/load 的临时目录。

    Returns:
        dict[str, Any]: 测量结果。
    """
    print(f"[{name}] 构建索引...", flush=True)
    t_build_start = time.perf_counter()
    backend, build_params = build_backend(name, base, metric)
    build_seconds = time.perf_counter() - t_build_start
    memory_mb = float(backend.memory_mb())

    index_path = tmp_dir / f"{name}.idx"
    save_seconds = 0.0
    try:
        t_save_start = time.perf_counter()
        backend.save(str(index_path))
        save_seconds = time.perf_counter() - t_save_start
    except Exception as exc:
        print(f"[{name}] save 失败: {exc}", flush=True)

    print(f"[{name}] 构建完成 build={build_seconds:.2f}s mem={memory_mb:.1f}MB", flush=True)

    recall_results: dict[int, float] = {}
    extra_meta: dict[int, Any] = {}
    for k in top_k_list:
        labels, _ = backend.search(queries, k)
        if k in ground_truth:
            recall_results[k] = compute_recall(labels, ground_truth[k], k)
        else:
            recall_results[k] = float("nan")
        if hasattr(backend, "last_search_meta") and backend.last_search_meta:
            extra_meta[k] = dict(backend.last_search_meta)
            extra_meta[k].pop("ef_per_query", None)
            extra_meta[k].pop("retries_per_query", None)

    latency_table: dict[str, dict[str, dict[str, float]]] = {}
    for k in top_k_list:
        latency_table[str(k)] = {}
        for cc in concurrency_list:
            print(f"[{name}] 延迟测试 k={k} conc={cc}", flush=True)
            latency_table[str(k)][str(cc)] = run_latency(backend, queries, k, cc)

    return {
        "name": name,
        "build_seconds": build_seconds,
        "save_seconds": save_seconds,
        "memory_mb": memory_mb,
        "build_params": build_params,
        "recall": {str(k): v for k, v in recall_results.items()},
        "adaptive_meta": {str(k): v for k, v in extra_meta.items()},
        "latency": latency_table,
    }


def build_ground_truth(
    base: np.ndarray, queries: np.ndarray, top_k_list: list[int], metric: str
) -> dict[int, np.ndarray]:
    """用 brute 后端预计算 ground truth。

    Args:
        base: 底库向量。
        queries: 查询向量。
        top_k_list: 待评估的 ``top_k`` 列表。
        metric: 距离度量。

    Returns:
        dict[int, np.ndarray]: ``{k: indices(M, k)}``。
    """
    print("[gt] 计算 ground truth (brute)...", flush=True)
    gt_backend, _ = build_backend("brute", base, metric)
    max_k = max(top_k_list)
    labels, _ = gt_backend.search(queries, max_k)
    return {k: labels[:, :k].copy() for k in top_k_list}


def fmt_float(value: float, digits: int = 3) -> str:
    """简洁地格式化浮点。"""
    if value is None:
        return "-"
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return "-"
    return f"{value:.{digits}f}"


def render_markdown(results: dict[str, Any], output_path: Path) -> None:
    """将基准结果渲染为 Markdown 报告。

    Args:
        results: ``run_benchmark`` 返回的字典。
        output_path: 报告输出路径。
    """
    cfg = results["config"]
    env = results["env"]
    backends = results["backends"]

    lines: list[str] = []
    add = lines.append

    add("# ANN 后端性能基准报告")
    add("")
    add("## 1. 实验目的")
    add("")
    add(
        "对比 `brute`、`hnswlib`、`faiss-hnsw`、`faiss-ivfpq` 与改进版 "
        "`adaptive-hnsw` 在单细胞 PCA 向量场景下的检索性能。"
        "评估指标涵盖**构建耗时**、**内存占用**、**召回率 Recall@k** 以及"
        "**不同并发度下的延迟分位与吞吐 QPS**，"
        "为生产部署的后端选型提供数据支撑。"
    )
    add("")

    add("## 2. 实验环境")
    add("")
    add("| 项目 | 取值 |")
    add("| ---- | ---- |")
    add(f"| 操作系统 | `{env['platform']}` |")
    add(f"| 架构 | `{env['machine']}` |")
    add(f"| 处理器 | `{env['processor']}` |")
    add(f"| 逻辑核心数 | `{env['cpu_count']}` |")
    add(f"| Python | `{env['python']}` |")
    add(f"| numpy | `{env['numpy']}` |")
    add(f"| faiss | `{env['faiss']}` |")
    add(f"| hnswlib | `{env['hnswlib']}` |")
    add("")

    add("## 3. 实验设置")
    add("")
    add(f"- 数据来源：{cfg['data_source']}")
    add(f"- 底库规模 N = `{cfg['n']}`")
    add(f"- 向量维度 dim = `{cfg['dim']}`")
    add(f"- 查询数量 M = `{cfg['queries']}`")
    add(f"- top_k = `{cfg['top_k']}`")
    add(f"- 并发度 = `{cfg['concurrency']}`")
    add(f"- 距离度量 = `{cfg['metric']}`")
    add(f"- 随机种子 = `{cfg['seed']}`")
    add("")

    add("## 4. 实验结果")
    add("")
    add("### 4.1 索引构建")
    add("")
    add("| backend | build_seconds | save_seconds | memory_mb | build_params |")
    add("| ------- | ------------- | ------------ | --------- | ------------ |")
    for item in backends:
        add(
            "| {name} | {build} | {save} | {mem} | `{params}` |".format(
                name=item["name"],
                build=fmt_float(item["build_seconds"]),
                save=fmt_float(item["save_seconds"]),
                mem=fmt_float(item["memory_mb"], 2),
                params=json.dumps(item["build_params"], ensure_ascii=False),
            )
        )
    add("")

    add("### 4.2 召回率")
    add("")
    top_k_cols = cfg["top_k"]
    header = "| backend | " + " | ".join(f"Recall@{k}" for k in top_k_cols) + " |"
    sep = "| --- | " + " | ".join("---" for _ in top_k_cols) + " |"
    add(header)
    add(sep)
    for item in backends:
        recall_cells = [fmt_float(item["recall"].get(str(k)), 4) for k in top_k_cols]
        add(f"| {item['name']} | " + " | ".join(recall_cells) + " |")
    add("")
    add(
        "> 说明：`brute` 自身作为 ground truth，Recall 恒为 `1.0`；"
        "`faiss-ivfpq` 由于量化损失，召回率通常显著低于图索引。"
    )
    add("")

    add("### 4.3 单次延迟与吞吐")
    add("")
    for item in backends:
        add(f"#### {item['name']}")
        add("")
        for k in top_k_cols:
            add(f"**top_k = {k}**")
            add("")
            add("| concurrency | p50_ms | p95_ms | p99_ms | mean_ms | QPS |")
            add("| ----------- | ------ | ------ | ------ | ------- | --- |")
            k_table = item["latency"].get(str(k), {})
            for cc in cfg["concurrency"]:
                row = k_table.get(str(cc), {})
                add(
                    "| {cc} | {p50} | {p95} | {p99} | {mean} | {qps} |".format(
                        cc=cc,
                        p50=fmt_float(row.get("p50_ms"), 3),
                        p95=fmt_float(row.get("p95_ms"), 3),
                        p99=fmt_float(row.get("p99_ms"), 3),
                        mean=fmt_float(row.get("mean_ms"), 3),
                        qps=fmt_float(row.get("qps"), 1),
                    )
                )
            add("")
        if item.get("adaptive_meta"):
            add("**自适应元数据（mean_ef / max_ef_used / max_retries）**")
            add("")
            add("| top_k | mean_ef | max_ef_used | max_retries |")
            add("| ----- | ------- | ----------- | ----------- |")
            for k in top_k_cols:
                meta = item["adaptive_meta"].get(str(k), {})
                add(
                    "| {k} | {me} | {mu} | {mr} |".format(
                        k=k,
                        me=fmt_float(meta.get("mean_ef"), 1),
                        mu=meta.get("max_ef_used", "-"),
                        mr=meta.get("max_retries", "-"),
                    )
                )
            add("")

    add("## 5. 分析与结论")
    add("")
    add(_render_analysis(results))
    add("")

    add("## 6. 后续工作")
    add("")
    add(
        "- **GPU 加速**：将 `faiss-cpu` 替换为 `faiss-gpu`，"
        "对大批量查询可获得 5~20× 吞吐提升。"
    )
    add(
        "- **更细的自适应策略**：基于历史 query 分布在线学习 `ef` 初值，"
        "或引入 PI 控制器跟踪目标 recall。"
    )
    add(
        "- **量化精度可调**：为 IVF-PQ 引入 OPQ 旋转 + 重排序 (re-ranking) "
        "层挽回召回损失。"
    )
    add("- **持久化与冷启动**：评估索引文件大小与冷加载耗时，纳入综合指标。")
    add("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def _render_analysis(results: dict[str, Any]) -> str:
    """根据实测结果生成中文分析段落。"""
    cfg = results["config"]
    items = {item["name"]: item for item in results["backends"]}
    paragraphs: list[str] = []

    def latency_at(item: dict[str, Any], k: int, cc: int) -> float:
        return float(item["latency"].get(str(k), {}).get(str(cc), {}).get("p95_ms", float("nan")))

    def recall_at(item: dict[str, Any], k: int) -> float:
        return float(item["recall"].get(str(k), float("nan")))

    top_k_cols = cfg["top_k"]
    k_eval = top_k_cols[0]
    cc_eval = cfg["concurrency"][0]

    if "hnswlib" in items and "faiss-hnsw" in items:
        lib = items["hnswlib"]
        fh = items["faiss-hnsw"]
        paragraphs.append(
            "- **HNSWLIB vs FAISS-HNSW**：两者均为图索引，"
            f"在 top_k={k_eval}、并发={cc_eval} 下 `hnswlib` p95={fmt_float(latency_at(lib, k_eval, cc_eval))}ms，"
            f"`faiss-hnsw` p95={fmt_float(latency_at(fh, k_eval, cc_eval))}ms；"
            f"召回 Recall@{k_eval} 分别为 {fmt_float(recall_at(lib, k_eval), 4)} 与 {fmt_float(recall_at(fh, k_eval), 4)}。"
            "实践中 `hnswlib` 实现更轻、内存友好，`faiss-hnsw` 借助 OMP 线程在大批量查询时更稳定。"
        )

    if "faiss-ivfpq" in items:
        ip = items["faiss-ivfpq"]
        paragraphs.append(
            "- **IVF-PQ 内存优势 vs 召回损失**："
            f"`faiss-ivfpq` 内存仅 {fmt_float(ip['memory_mb'], 2)}MB（约为图索引的几十分之一），"
            f"但 Recall@{k_eval} 仅 {fmt_float(recall_at(ip, k_eval), 4)}，"
            "适合内存极度受限或对召回要求较低的"
            "大规模冷数据召回层；如需高召回应叠加重排序。"
        )

    if "adaptive-hnsw" in items:
        ad = items["adaptive-hnsw"]
        base = items.get("hnswlib")
        meta_str = ""
        meta_block = ad.get("adaptive_meta", {}).get(str(k_eval))
        if meta_block:
            meta_str = (
                f"平均最终 ef={fmt_float(meta_block.get('mean_ef'), 1)}，"
                f"最大重试次数={meta_block.get('max_retries', '-')}。"
            )
        cmp_str = ""
        if base is not None:
            cmp_str = (
                f"对比 `hnswlib` 固定 `ef_search=64`，p95 变化 "
                f"{fmt_float(latency_at(base, k_eval, cc_eval))}ms → "
                f"{fmt_float(latency_at(ad, k_eval, cc_eval))}ms，"
                f"Recall@{k_eval} {fmt_float(recall_at(base, k_eval), 4)} → "
                f"{fmt_float(recall_at(ad, k_eval), 4)}。"
            )
        paragraphs.append(
            "- **Adaptive HNSW**：通过相对距离间隔判定是否需要扩大 `ef_search` 并按 query 粒度提前返回。"
            f"{meta_str}{cmp_str}"
            "在 query 难度分布差异较大的场景下，可在不显著牺牲 p95 的前提下保持高召回；"
            "对易查询样本提前停止，从而降低平均延迟。"
        )

    paragraphs.append(
        "- **推荐场景**："
        "（1）小规模 + 高召回首选 `hnswlib` 或 `faiss-hnsw`；"
        "（2）超大规模、内存受限可选 `faiss-ivfpq`；"
        "（3）query 难度分布不均、希望兼顾尾延迟与召回时使用 `adaptive-hnsw`；"
        "（4）评测 ground truth 与小型调试用 `brute`。"
    )

    if not paragraphs:
        return "（未跑足够后端，分析略。）"
    return "\n".join(paragraphs)


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    """执行完整基准流程。

    Args:
        args: 命令行参数。

    Returns:
        dict[str, Any]: 测量结果总览。
    """
    base, queries, source_desc = load_vectors(args)
    dim = base.shape[1]
    top_k_list = [int(x) for x in args.top_k.split(",") if x.strip()]
    concurrency_list = [int(x) for x in args.concurrency.split(",") if x.strip()]
    backend_names = [x.strip() for x in args.backends.split(",") if x.strip()]

    print(
        f"配置: n={args.n} dim={dim} queries={args.queries} "
        f"top_k={top_k_list} conc={concurrency_list} backends={backend_names}",
        flush=True,
    )

    ground_truth: dict[int, np.ndarray] = {}
    if not args.skip_ground_truth:
        ground_truth = build_ground_truth(base, queries, top_k_list, args.metric)

    with tempfile.TemporaryDirectory(prefix="ann_bench_") as tmp:
        tmp_dir = Path(tmp)
        backend_results: list[dict[str, Any]] = []
        for name in backend_names:
            try:
                result = measure_backend(
                    name=name,
                    base=base,
                    queries=queries,
                    top_k_list=top_k_list,
                    concurrency_list=concurrency_list,
                    metric=args.metric,
                    ground_truth=ground_truth,
                    tmp_dir=tmp_dir,
                )
                backend_results.append(result)
            except Exception as exc:
                print(f"[{name}] 跑测失败: {exc}", flush=True)
                backend_results.append({"name": name, "error": str(exc)})

    return {
        "env": collect_env_info(),
        "config": {
            "n": int(args.n),
            "dim": int(dim),
            "queries": int(args.queries),
            "top_k": top_k_list,
            "concurrency": concurrency_list,
            "metric": args.metric,
            "seed": int(args.seed),
            "backends": backend_names,
            "data_source": source_desc,
        },
        "backends": [b for b in backend_results if "error" not in b],
        "errors": [b for b in backend_results if "error" in b],
    }


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="ANN 后端基准测试")
    parser.add_argument("--n", type=int, default=50000, help="底库向量条数")
    parser.add_argument("--dim", type=int, default=50, help="合成向量维度")
    parser.add_argument("--queries", type=int, default=1000, help="查询数量")
    parser.add_argument("--top-k", type=str, default="10,100", help="top_k 列表，逗号分隔")
    parser.add_argument(
        "--concurrency", type=str, default="1,4,8", help="并发度列表，逗号分隔"
    )
    parser.add_argument(
        "--backends",
        type=str,
        default="brute,hnswlib,faiss-hnsw,faiss-ivfpq,adaptive-hnsw",
        help="参与对比的后端，逗号分隔",
    )
    parser.add_argument("--metric", type=str, default="l2", help="距离度量")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument(
        "--output",
        type=str,
        default=str(PROJECT_ROOT / "docs" / "benchmark_results.json"),
        help="JSON 结果输出路径",
    )
    parser.add_argument(
        "--report",
        type=str,
        default=str(PROJECT_ROOT / "docs" / "benchmark_report.md"),
        help="Markdown 报告输出路径",
    )
    parser.add_argument(
        "--use-liver", action="store_true", help="若指定则加载 liver.h5ad 的 obsm['X_pca']"
    )
    parser.add_argument("--liver-path", type=str, default=None, help="liver.h5ad 路径覆盖")
    parser.add_argument(
        "--skip-ground-truth",
        action="store_true",
        help="跳过 ground truth 计算（仅做延迟测试，召回会显示为 NaN）",
    )
    return parser.parse_args()


def main() -> int:
    """脚本入口。"""
    args = parse_args()
    results = run_benchmark(args)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"结果已写入 {output_path}", flush=True)

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    render_markdown(results, report_path)
    print(f"报告已写入 {report_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
