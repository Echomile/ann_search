"""把 sweep_offline.py 产出的 JSON 渲染为 matplotlib PNG，供 docs / PPT 使用。

典型用法::

    cd backend && uv run python scripts/sweep_plot.py \\
        --in docs/sweep_real_liver_pca30.json \\
        --out docs/assets/benchmark/pareto_pca30.png

输出: 一张 recall-QPS 帕累托散点 + 前沿连线图, 按 backend 分组着色。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = BACKEND_DIR.parent

BACKEND_COLORS = {
    "hnswlib": "#1677ff",
    "faiss-hnsw": "#52c41a",
    "faiss-ivfpq": "#fa8c16",
    "adaptive-hnsw": "#722ed1",
    "brute": "#8c8c8c",
    "sparse-brute": "#13c2c2",
}


def parse_args() -> argparse.Namespace:
    """解析命令行。"""
    parser = argparse.ArgumentParser(description="渲染 sweep JSON 为 recall-QPS 帕累托 PNG")
    parser.add_argument(
        "--in",
        dest="in_path",
        type=str,
        required=True,
        help="输入 sweep JSON 路径（相对项目根或绝对）",
    )
    parser.add_argument(
        "--out",
        type=str,
        required=True,
        help="输出 PNG 路径（相对项目根或绝对）",
    )
    parser.add_argument("--dpi", type=int, default=160, help="导出 DPI，默认 160")
    parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="自定义图标题，缺省自动从 JSON metadata 生成",
    )
    return parser.parse_args()


def _resolve(path_str: str) -> Path:
    """把相对项目根的路径转绝对路径。"""
    p = Path(path_str)
    return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()


def main() -> None:
    """主入口：读取 JSON → 画图 → 保存。"""
    args = parse_args()
    in_path = _resolve(args.in_path)
    out_path = _resolve(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data = json.loads(in_path.read_text())
    points = data["points"]
    title_meta = (
        f"N={data['n']}, dim={data['dim']}, queries={data['queries']}, "
        f"top_k={data['top_k']}, source={data['data_source']}"
    )
    title = args.title or f"recall-QPS Pareto Curve ({title_meta})"

    fig, ax = plt.subplots(figsize=(10, 6), dpi=args.dpi)

    by_backend: dict[str, list[dict]] = {}
    for p in points:
        by_backend.setdefault(p["backend"], []).append(p)

    for backend, pts in by_backend.items():
        color = BACKEND_COLORS.get(backend, "#999")
        pareto_pts = [p for p in pts if p.get("on_pareto")]
        non_pareto_pts = [p for p in pts if not p.get("on_pareto")]
        if non_pareto_pts:
            ax.scatter(
                [p["recall"] for p in non_pareto_pts],
                [p["qps"] for p in non_pareto_pts],
                c=color,
                s=40,
                alpha=0.55,
                label=f"{backend}",
                edgecolors="white",
                linewidths=0.5,
            )
        if pareto_pts:
            ax.scatter(
                [p["recall"] for p in pareto_pts],
                [p["qps"] for p in pareto_pts],
                c=color,
                s=180,
                marker="*",
                label=f"{backend} (on Pareto)",
                edgecolors="white",
                linewidths=1.0,
            )

    # 全局帕累托前沿连线
    pareto_global = sorted([p for p in points if p.get("on_pareto")], key=lambda p: p["recall"])
    if len(pareto_global) > 1:
        ax.plot(
            [p["recall"] for p in pareto_global],
            [p["qps"] for p in pareto_global],
            color="#ff4d4f",
            linestyle="--",
            linewidth=1.5,
            alpha=0.7,
            label="Pareto frontier",
            zorder=0,
        )

    ax.set_xlabel(f"Recall@{data['top_k']}")
    ax.set_ylabel("QPS (concurrency=1, log scale)")
    ax.set_yscale("log")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(loc="lower left", fontsize=8, ncols=2)
    ax.set_title(title, fontsize=11)
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)

    print(f"输出 -> {out_path} ({out_path.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
