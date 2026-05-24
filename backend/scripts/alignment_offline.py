"""离线跨数据集对齐 + 检索对比脚本 (v1.2 D7 polish)。

把 :mod:`backend/data/processed/3/vectors.npy` (PCA 30D 真实数据) 切成 N 个
"虚拟数据集" 模拟跨数据集场景, 横向对比 3 种检索策略:

- ``baseline_minmax``: 每个虚拟数据集独立 PCA + whitening 后各自跑 hnswlib,
  把每个 split 的 top-K 距离做 min-max 归一化后合并取最近 K 个 (v1.0 兼容路径).
- ``intersect_only``: 把所有 split 拼回去重新跑统一 PCA(target_dim) 模拟 D7
  "对齐", 在统一向量空间上跑 hnswlib 单库查询.
- ``harmony`` (可选): 在 intersect_only 之上调 ``harmonypy.run_harmony`` 做
  batch 校正; ``harmonypy`` 缺失时该项标记 ``skipped``.

为了让 ``baseline_minmax`` 体现 "向量空间不可比" 的劣势, 我们对每个虚拟数据集
独立 fit ``sklearn.decomposition.PCA(n_components=dim, whiten=True)``, 等效于
不同实验室/不同 HVG 选择造成的主轴旋转 + 方差归一化; 跨 split 的距离仅靠
min-max 难以完美对齐.

输出 JSON 含每个策略的 ``recall@K`` / ``cross_dataset_coverage`` (top-K 来自
几个不同 split) / 延迟分位 / QPS / 估算内存, 供 ``docs/benchmark_report.md``
§9 真实数据回填使用.

典型用法::

    cd backend && uv run python scripts/alignment_offline.py \\
        --vectors_path data/processed/3/vectors.npy \\
        --n_splits 3 --queries 100 --top_k 10 \\
        --out ../docs/alignment_offline_3way.json
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.ann.brute_backend import BruteBackend  # noqa: E402
from app.services.ann.hnswlib_backend import HnswlibBackend  # noqa: E402


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="离线跨数据集对齐 + 检索对比 (baseline_minmax vs intersect_only vs harmony)",
    )
    parser.add_argument(
        "--vectors_path",
        type=str,
        default="backend/data/processed/3/vectors.npy",
        help="PCA 30D 向量 npy 路径 (相对项目根或绝对)",
    )
    parser.add_argument(
        "--n_splits",
        type=int,
        default=3,
        help="把全库切成几个虚拟数据集",
    )
    parser.add_argument(
        "--queries",
        type=int,
        default=100,
        help="抽取的查询条数, 从全库均匀随机采样",
    )
    parser.add_argument("--top_k", type=int, default=10, help="Top-K")
    parser.add_argument(
        "--ef_search",
        type=int,
        default=128,
        help="hnswlib 查询期 ef_search (三种策略共享)",
    )
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument(
        "--out",
        type=str,
        default="docs/alignment_offline_3way.json",
        help="输出 JSON 路径 (相对项目根或绝对)",
    )
    return parser.parse_args()


def split_into_virtual_datasets(
    n_total: int,
    n_splits: int,
    seed: int,
) -> list[np.ndarray]:
    """随机切分全库行号为 ``n_splits`` 个虚拟数据集。

    Args:
        n_total: 全库大小。
        n_splits: 切分份数。
        seed: 随机种子。

    Returns:
        list[np.ndarray]: 每个虚拟数据集对应的 global indices (``int64``),
            列表长度等于 ``n_splits``, 总和等于 ``n_total``。
    """
    rng = np.random.default_rng(seed)
    perm = rng.permutation(n_total)
    return [arr.astype(np.int64) for arr in np.array_split(perm, n_splits)]


def fit_local_preprocess(vectors: np.ndarray, seed: int) -> tuple[Any, np.ndarray]:
    """模拟单数据集独立预处理: ``PCA(dim, whiten=True)``。

    每个虚拟数据集都各跑一次 PCA, 在保持维度不变的同时引入与全局 PCA 不一致
    的主轴旋转 + 各维度方差归一化, 等效于模拟 "不同实验室 / 不同 HVG 选择"
    造成的向量空间错位。

    Args:
        vectors: 该 split 的原始向量, shape ``(n_local, dim)``。
        seed: 随机种子, 透传到 sklearn PCA 的 ``random_state``。

    Returns:
        tuple[Any, np.ndarray]: ``(已 fit 的 sklearn PCA, 变换后的 local 向量)``。
    """
    from sklearn.decomposition import PCA

    dim = int(vectors.shape[1])
    pca = PCA(n_components=dim, whiten=True, random_state=int(seed))
    local = pca.fit_transform(vectors).astype(np.float32, copy=False)
    return pca, np.ascontiguousarray(local)


def build_hnswlib_index(vectors: np.ndarray, *, ef_search: int) -> HnswlibBackend:
    """构建一个 hnswlib (``l2``) 后端实例。

    Args:
        vectors: ``(N, D)`` 向量, ``float32``。
        ef_search: 查询期 ef_search。

    Returns:
        HnswlibBackend: 已 build 完成的实例。
    """
    backend = HnswlibBackend(dim=int(vectors.shape[1]), metric="l2")
    backend.build(vectors, M=16, ef_construction=200, ef_search=int(ef_search))
    return backend


def _percentile_ms(latencies: list[float], p: float) -> float:
    """计算延迟列表的 p 百分位 (单位 ms)。"""
    if not latencies:
        return 0.0
    return float(np.percentile(np.asarray(latencies, dtype=np.float64), p))


def run_baseline_minmax(
    *,
    splits_global_idx: list[np.ndarray],
    splits_local_vec: list[np.ndarray],
    splits_local_pca: list[Any],
    query_orig: np.ndarray,
    top_k: int,
    ef_search: int,
) -> dict[str, Any]:
    """跑 baseline 策略: 各自查 + min-max 归一化重排。

    对每个虚拟数据集 ``i``:
        1. 用 ``splits_local_vec[i]`` 构建 hnswlib;
        2. ``q_local_i = splits_local_pca[i].transform(q_orig)`` 投影 query 到 split 局部空间;
        3. 查 top-K 得到 ``(local_indices_i, distances_i)``;
        4. 把 ``distances_i`` 做 min-max 归一化到 ``[0, 1]``.

    把 ``n_splits * top_k`` 个候选合并, 取归一化距离最小的 ``top_k`` 个,
    映射回 global 行号。

    Args:
        splits_global_idx: 每个 split 的 global indices, 用来 local->global 映射。
        splits_local_vec: 每个 split 的 local PCA 后向量, 用来建索引。
        splits_local_pca: 每个 split 已 fit 完毕的 PCA 对象。
        query_orig: ``(M, D)`` 条 query 在原始 PCA 30D 空间。
        top_k: Top-K。
        ef_search: hnswlib ef_search。

    Returns:
        dict[str, Any]: 含 ``top_k_global_idx`` ``(M, top_k)`` int64
            + ``mem_mb`` (各 split 索引内存求和) + ``p50_ms / p95_ms / p99_ms / qps``。
    """
    backends = [build_hnswlib_index(v, ef_search=ef_search) for v in splits_local_vec]
    mem_mb = float(sum(b.memory_mb() for b in backends))
    q_local_list = [
        np.ascontiguousarray(pca.transform(query_orig).astype(np.float32, copy=False))
        for pca in splits_local_pca
    ]

    num_queries = int(query_orig.shape[0])
    n_splits = len(splits_global_idx)
    final_top_k = np.zeros((num_queries, top_k), dtype=np.int64)
    latencies: list[float] = []
    wall_start = time.perf_counter()
    for qi in range(num_queries):
        t0 = time.perf_counter()
        cand_global: list[int] = []
        cand_norm_dist: list[float] = []
        for si in range(n_splits):
            q = q_local_list[si][qi : qi + 1]
            idx_local, dist_local = backends[si].search(q, top_k)
            idx_local = idx_local[0]
            dist_local = dist_local[0]
            d_min = float(dist_local.min())
            d_max = float(dist_local.max())
            denom = d_max - d_min if d_max > d_min else 1.0
            dist_norm = (dist_local - d_min) / denom
            global_map = splits_global_idx[si]
            for k in range(top_k):
                cand_global.append(int(global_map[int(idx_local[k])]))
                cand_norm_dist.append(float(dist_norm[k]))
        order = np.argsort(np.asarray(cand_norm_dist))[:top_k]
        final_top_k[qi] = np.asarray([cand_global[i] for i in order], dtype=np.int64)
        latencies.append((time.perf_counter() - t0) * 1000.0)
    wall_elapsed = time.perf_counter() - wall_start
    qps = num_queries / wall_elapsed if wall_elapsed > 0 else 0.0

    return {
        "top_k_global_idx": final_top_k,
        "mem_mb": mem_mb,
        "p50_ms": _percentile_ms(latencies, 50),
        "p95_ms": _percentile_ms(latencies, 95),
        "p99_ms": _percentile_ms(latencies, 99),
        "qps": float(qps),
    }


def run_intersect_only(
    *,
    vectors_full: np.ndarray,
    splits_global_idx: list[np.ndarray],
    query_global_idx: np.ndarray,
    top_k: int,
    ef_search: int,
    seed: int,
) -> dict[str, Any]:
    """跑 intersect_only 策略: 拼接所有 split 重跑统一 PCA + hnswlib 单库查询。

    模拟 :mod:`app.services.alignment` 的 ``intersect_only`` 路径在 PCA 30D
    空间的简化版: 直接把所有虚拟数据集的原始向量拼接 (顺序按 ``splits_global_idx``),
    用统一 ``PCA(target_dim=dim, whiten=True)`` 生成对齐空间, 然后在该空间上跑
    hnswlib 单库检索。query 也用同一 PCA 投影。

    Args:
        vectors_full: 原始全库向量 ``(N, D)``。
        splits_global_idx: 每个 split 的 global indices, 用于按 split 顺序拼接。
        query_global_idx: query 的 global 行号, ``(M,)``。
        top_k: Top-K。
        ef_search: hnswlib ef_search。
        seed: 随机种子。

    Returns:
        dict[str, Any]: 同 :func:`run_baseline_minmax`. 注意 ``top_k_global_idx``
            是先在 aligned 库内得 top-K 行号再通过拼接顺序映射回 global。
    """
    from sklearn.decomposition import PCA

    dim = int(vectors_full.shape[1])
    concat_idx = np.concatenate(splits_global_idx).astype(np.int64)
    concat_vec = vectors_full[concat_idx]

    pca_aligned = PCA(n_components=dim, whiten=False, random_state=int(seed))
    aligned_vec = np.ascontiguousarray(
        pca_aligned.fit_transform(concat_vec).astype(np.float32, copy=False)
    )

    query_orig = vectors_full[query_global_idx]
    query_aligned = np.ascontiguousarray(
        pca_aligned.transform(query_orig).astype(np.float32, copy=False)
    )

    backend = build_hnswlib_index(aligned_vec, ef_search=ef_search)
    mem_mb = float(backend.memory_mb())

    num_queries = int(query_aligned.shape[0])
    latencies: list[float] = []
    final_top_k_aligned = np.zeros((num_queries, top_k), dtype=np.int64)
    wall_start = time.perf_counter()
    for qi in range(num_queries):
        t0 = time.perf_counter()
        idx_a, _ = backend.search(query_aligned[qi : qi + 1], top_k)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        final_top_k_aligned[qi] = idx_a[0].astype(np.int64)
    wall_elapsed = time.perf_counter() - wall_start
    qps = num_queries / wall_elapsed if wall_elapsed > 0 else 0.0

    final_top_k_global = concat_idx[final_top_k_aligned]
    return {
        "top_k_global_idx": final_top_k_global,
        "mem_mb": mem_mb,
        "p50_ms": _percentile_ms(latencies, 50),
        "p95_ms": _percentile_ms(latencies, 95),
        "p99_ms": _percentile_ms(latencies, 99),
        "qps": float(qps),
    }


def run_harmony(
    *,
    vectors_full: np.ndarray,
    splits_global_idx: list[np.ndarray],
    query_global_idx: np.ndarray,
    top_k: int,
    ef_search: int,
    seed: int,
) -> dict[str, Any] | None:
    """跑 harmony 策略: 在 intersect_only 之上做 ``harmonypy.run_harmony`` batch 校正。

    依赖 ``harmonypy``, 若未安装直接返回 ``None``, 调用方应将该项标记为
    ``skipped`` 并在最终 JSON 给出降级说明。

    实现注意:
        :mod:`harmonypy` 没有 ``transform`` 接口校正新样本, 这里 query 仍用
        ``pca_aligned.transform`` 投影到 aligned 空间, 直接在 harmony 校正后的
        底库上检索 (近似于 harmony 主要在底库侧消除批次, query 侧偏移可忽略)。

    Args:
        vectors_full: 原始全库向量。
        splits_global_idx: 每个 split 的 global indices。
        query_global_idx: query 的 global 行号。
        top_k: Top-K。
        ef_search: hnswlib ef_search。
        seed: 随机种子。

    Returns:
        dict[str, Any] | None: 同 :func:`run_intersect_only`, 缺包时返回 ``None``。
    """
    try:
        import harmonypy
    except ImportError:
        return None

    import pandas as pd
    from sklearn.decomposition import PCA

    dim = int(vectors_full.shape[1])
    concat_idx = np.concatenate(splits_global_idx).astype(np.int64)
    concat_vec = vectors_full[concat_idx]

    pca_aligned = PCA(n_components=dim, whiten=False, random_state=int(seed))
    aligned_vec = pca_aligned.fit_transform(concat_vec).astype(np.float32, copy=False)

    batch_labels: list[str] = []
    for si, idx in enumerate(splits_global_idx):
        batch_labels.extend([f"batch_{si}"] * int(idx.shape[0]))
    meta = pd.DataFrame({"batch": batch_labels})

    ho = harmonypy.run_harmony(aligned_vec, meta, vars_use=["batch"], random_state=int(seed))
    harmonized = np.ascontiguousarray(np.asarray(ho.Z_corr.T, dtype=np.float32))

    query_orig = vectors_full[query_global_idx]
    query_aligned = np.ascontiguousarray(
        pca_aligned.transform(query_orig).astype(np.float32, copy=False)
    )

    backend = build_hnswlib_index(harmonized, ef_search=ef_search)
    mem_mb = float(backend.memory_mb())

    num_queries = int(query_aligned.shape[0])
    latencies: list[float] = []
    final_top_k_aligned = np.zeros((num_queries, top_k), dtype=np.int64)
    wall_start = time.perf_counter()
    for qi in range(num_queries):
        t0 = time.perf_counter()
        idx_a, _ = backend.search(query_aligned[qi : qi + 1], top_k)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        final_top_k_aligned[qi] = idx_a[0].astype(np.int64)
    wall_elapsed = time.perf_counter() - wall_start
    qps = num_queries / wall_elapsed if wall_elapsed > 0 else 0.0

    final_top_k_global = concat_idx[final_top_k_aligned]
    return {
        "top_k_global_idx": final_top_k_global,
        "mem_mb": mem_mb,
        "p50_ms": _percentile_ms(latencies, 50),
        "p95_ms": _percentile_ms(latencies, 95),
        "p99_ms": _percentile_ms(latencies, 99),
        "qps": float(qps),
    }


def recall_at_k(predicted: np.ndarray, truth: np.ndarray) -> float:
    """逐条 query 的 ``|预测 ∩ 真值| / |真值|`` 求均值。

    Args:
        predicted: ``(M, k)`` 预测 top-K 的 global 行号。
        truth: ``(M, k)`` ground truth 的 global 行号。

    Returns:
        float: 平均 recall@k, ``[0, 1]``。
    """
    num_queries = int(predicted.shape[0])
    if num_queries == 0:
        return 0.0
    total = 0.0
    for i in range(num_queries):
        pred_set = {int(x) for x in predicted[i].tolist()}
        truth_set = {int(x) for x in truth[i].tolist()}
        if truth_set:
            total += len(pred_set & truth_set) / len(truth_set)
    return float(total / num_queries)


def cross_dataset_coverage(predicted: np.ndarray, split_label_of: np.ndarray) -> float:
    """每条 query 的 top-K 来自几个不同 split 的均值。

    Args:
        predicted: ``(M, top_k)`` global indices。
        split_label_of: ``(N,)`` 每个 global idx 对应的 split label (``0..n_splits-1``)。

    Returns:
        float: 平均覆盖的 split 数, 越大表示跨库混合度越高。
    """
    num_queries = int(predicted.shape[0])
    if num_queries == 0:
        return 0.0
    covs = []
    for i in range(num_queries):
        labels = split_label_of[predicted[i]]
        covs.append(int(np.unique(labels).shape[0]))
    return float(np.mean(covs))


def main() -> None:
    """脚本入口: 加载向量 -> 切分 -> 跑三策略 -> 写 JSON。"""
    args = parse_args()

    vp = Path(args.vectors_path)
    vp = vp if vp.is_absolute() else (PROJECT_ROOT / vp).resolve()
    if not vp.is_file():
        raise FileNotFoundError(f"vectors_path 不存在: {vp}")

    print(f"加载向量: {vp}")
    vectors = np.load(vp).astype(np.float32, copy=False)
    n_total, dim = int(vectors.shape[0]), int(vectors.shape[1])
    print(f"  shape=({n_total}, {dim})")

    print(f"切分为 {args.n_splits} 个虚拟数据集, seed={args.seed}")
    splits_global_idx = split_into_virtual_datasets(n_total, args.n_splits, args.seed)
    split_sizes = [int(arr.shape[0]) for arr in splits_global_idx]
    print(f"  split_sizes={split_sizes}")

    split_label_of = np.zeros(n_total, dtype=np.int32)
    for si, idx in enumerate(splits_global_idx):
        split_label_of[idx] = si

    print("拟合 per-split PCA + whitening 模拟独立预处理")
    splits_local_pca: list[Any] = []
    splits_local_vec: list[np.ndarray] = []
    for si, idx in enumerate(splits_global_idx):
        v_orig = vectors[idx]
        pca, v_local = fit_local_preprocess(v_orig, seed=args.seed + si)
        splits_local_pca.append(pca)
        splits_local_vec.append(v_local)
        print(f"  split {si}: orig {v_orig.shape} -> local {v_local.shape}")

    rng = np.random.default_rng(args.seed + 999)
    query_global_idx = rng.choice(n_total, size=args.queries, replace=False).astype(np.int64)
    query_orig = vectors[query_global_idx]
    print(f"抽取 {args.queries} 条 query (从全库均匀随机)")

    print("Ground Truth: 在原始全库上跑 brute (numba)")
    t0 = time.perf_counter()
    gt = BruteBackend(dim=dim, metric="l2")
    gt.build(vectors)
    truth_idx, _ = gt.search(query_orig, args.top_k)
    truth_idx = np.asarray(truth_idx, dtype=np.int64)
    print(f"  ground truth 耗时 {(time.perf_counter() - t0):.2f}s")

    print("\n=== Strategy A: baseline (各自查 + min-max) ===")
    res_baseline = run_baseline_minmax(
        splits_global_idx=splits_global_idx,
        splits_local_vec=splits_local_vec,
        splits_local_pca=splits_local_pca,
        query_orig=query_orig,
        top_k=args.top_k,
        ef_search=args.ef_search,
    )
    rec_baseline = recall_at_k(res_baseline["top_k_global_idx"], truth_idx)
    cov_baseline = cross_dataset_coverage(res_baseline["top_k_global_idx"], split_label_of)
    print(
        f"  recall@{args.top_k}={rec_baseline:.4f} cov={cov_baseline:.3f} "
        f"p50={res_baseline['p50_ms']:.3f}ms p95={res_baseline['p95_ms']:.3f}ms "
        f"qps={res_baseline['qps']:.1f} mem={res_baseline['mem_mb']:.2f}MB"
    )

    print("\n=== Strategy B: intersect_only (统一 PCA + 单库) ===")
    res_intersect = run_intersect_only(
        vectors_full=vectors,
        splits_global_idx=splits_global_idx,
        query_global_idx=query_global_idx,
        top_k=args.top_k,
        ef_search=args.ef_search,
        seed=args.seed,
    )
    rec_intersect = recall_at_k(res_intersect["top_k_global_idx"], truth_idx)
    cov_intersect = cross_dataset_coverage(res_intersect["top_k_global_idx"], split_label_of)
    print(
        f"  recall@{args.top_k}={rec_intersect:.4f} cov={cov_intersect:.3f} "
        f"p50={res_intersect['p50_ms']:.3f}ms p95={res_intersect['p95_ms']:.3f}ms "
        f"qps={res_intersect['qps']:.1f} mem={res_intersect['mem_mb']:.2f}MB"
    )

    print("\n=== Strategy C: harmony (依赖 harmonypy) ===")
    res_harmony = run_harmony(
        vectors_full=vectors,
        splits_global_idx=splits_global_idx,
        query_global_idx=query_global_idx,
        top_k=args.top_k,
        ef_search=args.ef_search,
        seed=args.seed,
    )
    if res_harmony is None:
        print("  harmonypy 未安装, 跳过 (优雅降级)")
        harmony_payload: dict[str, Any] = {
            "status": "skipped",
            "reason": "harmonypy not installed in current environment",
        }
    else:
        rec_harmony = recall_at_k(res_harmony["top_k_global_idx"], truth_idx)
        cov_harmony = cross_dataset_coverage(res_harmony["top_k_global_idx"], split_label_of)
        print(
            f"  recall@{args.top_k}={rec_harmony:.4f} cov={cov_harmony:.3f} "
            f"p50={res_harmony['p50_ms']:.3f}ms p95={res_harmony['p95_ms']:.3f}ms "
            f"qps={res_harmony['qps']:.1f} mem={res_harmony['mem_mb']:.2f}MB"
        )
        harmony_payload = {
            "status": "ok",
            "description": "intersect_only 之上调用 harmonypy.run_harmony 做 batch 校正",
            "recall_at_k": float(rec_harmony),
            "cross_dataset_coverage": float(cov_harmony),
            "p50_ms": res_harmony["p50_ms"],
            "p95_ms": res_harmony["p95_ms"],
            "p99_ms": res_harmony["p99_ms"],
            "qps": res_harmony["qps"],
            "mem_mb": res_harmony["mem_mb"],
        }

    truth_cov = cross_dataset_coverage(truth_idx, split_label_of)
    payload: dict[str, Any] = {
        "n_total": n_total,
        "dim": dim,
        "n_splits": int(args.n_splits),
        "split_sizes": split_sizes,
        "queries": int(args.queries),
        "top_k": int(args.top_k),
        "metric": "l2",
        "seed": int(args.seed),
        "ef_search": int(args.ef_search),
        "data_source": f"real:{vp.relative_to(PROJECT_ROOT)}"
        if PROJECT_ROOT in vp.parents
        else f"real:{vp.name}",
        "platform": {
            "system": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "numpy": np.__version__,
        },
        "ground_truth": {
            "cross_dataset_coverage": float(truth_cov),
            "note": "brute on full library, 表征真实近邻天然的 split 分布",
        },
        "strategies": {
            "baseline_minmax": {
                "description": "各 split 独立 PCA+whitening 后各自 hnswlib + min-max 距离归一化合并",
                "recall_at_k": float(rec_baseline),
                "cross_dataset_coverage": float(cov_baseline),
                "p50_ms": res_baseline["p50_ms"],
                "p95_ms": res_baseline["p95_ms"],
                "p99_ms": res_baseline["p99_ms"],
                "qps": res_baseline["qps"],
                "mem_mb": res_baseline["mem_mb"],
            },
            "intersect_only": {
                "description": "拼接所有 split 后跑统一 PCA + hnswlib 单库查询",
                "recall_at_k": float(rec_intersect),
                "cross_dataset_coverage": float(cov_intersect),
                "p50_ms": res_intersect["p50_ms"],
                "p95_ms": res_intersect["p95_ms"],
                "p99_ms": res_intersect["p99_ms"],
                "qps": res_intersect["qps"],
                "mem_mb": res_intersect["mem_mb"],
            },
            "harmony": harmony_payload,
        },
    }

    out_arg = Path(args.out)
    out_path = out_arg if out_arg.is_absolute() else (PROJECT_ROOT / out_arg).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\n输出 -> {out_path}")


if __name__ == "__main__":
    main()
