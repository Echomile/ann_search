"""离线参数扫描脚本 - 不依赖 DB / FastAPI 的 sweep CLI.

复用 :mod:`app.services.evaluation` 里的纯函数工具
(``_build_backend_for_sweep`` / ``_params_grid_for_backend`` / ``_apply_query_param``
/ ``_evaluate_single_point`` / ``_mark_pareto``), 在合成数据上跑一遍参数扫描,
输出 JSON 供 ``docs/benchmark_report.md`` §7 占位回填或 PPT 绘图使用.

典型用法::

    cd backend && uv run python scripts/sweep_offline.py \\
        --n 30000 --dim 30 --queries 200 --top_k 10 \\
        --out ../docs/sweep_offline_pca30.json

输出 JSON 结构::

    {
      "n": int, "dim": int, "queries": int, "top_k": int,
      "metric": "l2", "seed": int,
      "points": [
        {"backend": str, "params": dict, "recall": float, "qps": float,
         "p50_ms": float, "p95_ms": float, "p99_ms": float, "mem_mb": float,
         "on_pareto": bool},
        ...
      ]
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.ann.brute_backend import BruteBackend  # noqa: E402
from app.services.evaluation import (  # noqa: E402
    _apply_query_param,
    _build_backend_for_sweep,
    _evaluate_single_point,
    _mark_pareto,
    _params_grid_for_backend,
)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="离线 ANN 参数扫描（合成数据）")
    parser.add_argument("--n", type=int, default=30000, help="底库向量条数")
    parser.add_argument("--dim", type=int, default=30, help="向量维度")
    parser.add_argument("--queries", type=int, default=200, help="查询数")
    parser.add_argument("--top_k", type=int, default=10, help="Top-K")
    parser.add_argument(
        "--backends",
        type=str,
        default="hnswlib,faiss-hnsw,adaptive-hnsw,faiss-ivfpq,brute",
        help="逗号分隔的 backend 名列表",
    )
    parser.add_argument(
        "--ef_grid",
        type=str,
        default="16,32,64,128,256,512",
        help="ef_search 栅格 (逗号分隔, 应用于 hnswlib/faiss-hnsw/adaptive-hnsw)",
    )
    parser.add_argument(
        "--nprobe_grid",
        type=str,
        default="4,8,16,32,64,128",
        help="nprobe 栅格 (逗号分隔, 应用于 faiss-ivfpq)",
    )
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument(
        "--out",
        type=str,
        default="../docs/sweep_offline_pca30.json",
        help="输出 JSON 路径 (相对 backend 目录)",
    )
    return parser.parse_args()


def main() -> None:
    """跑离线 sweep 并写 JSON。"""
    args = parse_args()
    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    ef_grid = [int(x) for x in args.ef_grid.split(",") if x.strip()]
    nprobe_grid = [int(x) for x in args.nprobe_grid.split(",") if x.strip()]

    print(
        f"生成合成数据 standard_normal: N={args.n}, dim={args.dim}, queries={args.queries}, seed={args.seed}"
    )
    rng = np.random.default_rng(args.seed)
    base = rng.standard_normal((args.n, args.dim)).astype(np.float32)
    query_idx = rng.choice(args.n, size=args.queries, replace=False)
    queries = np.ascontiguousarray(base[query_idx], dtype=np.float32)

    print("构造 ground truth (brute) ...")
    gt = BruteBackend(dim=args.dim, metric="l2")
    gt.build(base)
    truth_indices, _ = gt.search(queries, args.top_k)
    truth_indices = np.asarray(truth_indices)

    collected: list[dict[str, Any]] = []
    for backend_name in backends:
        print(f"\n=== {backend_name} ===")
        if backend_name == "brute":
            metrics = _evaluate_single_point(gt, queries, truth_indices, top_k=args.top_k)
            point = {"backend": backend_name, "params": {}, **metrics}
            collected.append(point)
            print(
                f"  (no params): recall={point['recall']:.4f} "
                f"qps={point['qps']:.1f} p50={point['p50_ms']:.3f}ms"
            )
            continue

        backend = _build_backend_for_sweep(backend_name, base, "l2")
        grid = _params_grid_for_backend(backend_name, ef_grid, nprobe_grid)
        for params in grid:
            _apply_query_param(backend, backend_name, params)
            metrics = _evaluate_single_point(backend, queries, truth_indices, top_k=args.top_k)
            point = {"backend": backend_name, "params": dict(params), **metrics}
            collected.append(point)
            param_str = ", ".join(f"{k}={v}" for k, v in params.items()) or "—"
            print(
                f"  {param_str}: recall={point['recall']:.4f} "
                f"qps={point['qps']:.1f} p50={point['p50_ms']:.3f}ms mem={point['mem_mb']:.2f}MB"
            )

    pareto_flags = _mark_pareto([(p["recall"], p["qps"]) for p in collected])
    for p, on_pareto in zip(collected, pareto_flags, strict=True):
        p["on_pareto"] = bool(on_pareto)

    pareto_count = sum(pareto_flags)
    print(f"\n=== 总计 {len(collected)} 点, 帕累托前沿 {pareto_count} 点 ===")

    out_payload = {
        "n": args.n,
        "dim": args.dim,
        "queries": args.queries,
        "top_k": args.top_k,
        "metric": "l2",
        "seed": args.seed,
        "data_source": "synthetic_standard_normal",
        "points": collected,
    }
    out_path = (SCRIPT_DIR / args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out_payload, indent=2, ensure_ascii=False))
    print(f"\n输出 -> {out_path}")


if __name__ == "__main__":
    main()
