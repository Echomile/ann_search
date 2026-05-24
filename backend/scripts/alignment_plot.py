"""把 ``alignment_offline.py`` 产出的 JSON 渲染为 matplotlib PNG。

横向 bar chart, 三个子图分别展示 3 种检索策略在 ``recall@K`` / ``QPS`` /
``p50 延迟`` 三个维度的对比, 供 ``docs/benchmark_report.md`` §9 嵌入 PPT/演示。

典型用法::

    cd backend && uv run python scripts/alignment_plot.py \\
        --in docs/benchmark_data/alignment_offline_3way.json \\
        --out docs/assets/benchmark/alignment_3way.png
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

STRATEGY_LABELS = {
    "baseline_minmax": "baseline\n(min-max rerank)",
    "intersect_only": "intersect_only\n(unified PCA + single)",
    "harmony": "harmony\n(optional)",
}
STRATEGY_COLORS = {
    "baseline_minmax": "#8c8c8c",
    "intersect_only": "#1677ff",
    "harmony": "#722ed1",
}


def parse_args() -> argparse.Namespace:
    """解析命令行。"""
    parser = argparse.ArgumentParser(description="渲染 alignment_offline JSON 为对比 bar PNG")
    parser.add_argument(
        "--in",
        dest="in_path",
        type=str,
        default="docs/benchmark_data/alignment_offline_3way.json",
        help="输入 JSON 路径 (相对项目根或绝对)",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="docs/assets/benchmark/alignment_3way.png",
        help="输出 PNG 路径 (相对项目根或绝对)",
    )
    parser.add_argument("--dpi", type=int, default=150, help="PNG dpi")
    return parser.parse_args()


def _resolve(path_str: str) -> Path:
    """相对项目根 / 绝对路径都能解析。"""
    p = Path(path_str)
    return p if p.is_absolute() else (PROJECT_ROOT / p).resolve()


def _collect_points(payload: dict) -> list[dict]:
    """从 payload 抽取每个策略的指标行, 跳过 ``status=skipped`` 的项。"""
    rows: list[dict] = []
    for key, label in STRATEGY_LABELS.items():
        item = payload["strategies"].get(key, {})
        if item.get("status") == "skipped":
            continue
        if "recall_at_k" not in item:
            continue
        rows.append(
            {
                "key": key,
                "label": label,
                "recall": float(item["recall_at_k"]),
                "qps": float(item["qps"]),
                "p50_ms": float(item["p50_ms"]),
                "coverage": float(item["cross_dataset_coverage"]),
            }
        )
    return rows


def render(payload: dict, out_path: Path, dpi: int) -> None:
    """渲染 PNG 并落盘。

    Args:
        payload: alignment_offline JSON 反序列化后的 dict。
        out_path: PNG 落盘路径。
        dpi: 图像 dpi。
    """
    rows = _collect_points(payload)
    if not rows:
        raise RuntimeError("payload 中没有可绘制的策略行")

    labels = [r["label"] for r in rows]
    colors = [STRATEGY_COLORS[r["key"]] for r in rows]
    recalls = [r["recall"] for r in rows]
    qpss = [r["qps"] for r in rows]
    p50s = [r["p50_ms"] for r in rows]

    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5), constrained_layout=True)

    # Recall@K
    ax = axes[0]
    bars = ax.bar(labels, recalls, color=colors, width=0.6)
    ax.set_ylabel(f"Recall@{payload['top_k']}")
    ax.set_title(f"Recall@{payload['top_k']} (higher is better)")
    ax.set_ylim(0, 1.05)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    for b, v in zip(bars, recalls, strict=True):
        ax.text(
            b.get_x() + b.get_width() / 2,
            v + 0.02,
            f"{v:.3f}",
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold",
        )

    # QPS
    ax = axes[1]
    bars = ax.bar(labels, qpss, color=colors, width=0.6)
    ax.set_ylabel("QPS")
    ax.set_title("QPS (higher is better)")
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ymax = max(qpss) * 1.18 if qpss else 1.0
    ax.set_ylim(0, ymax)
    for b, v in zip(bars, qpss, strict=True):
        ax.text(
            b.get_x() + b.get_width() / 2,
            v + ymax * 0.01,
            f"{v:.0f}",
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold",
        )

    # p50 latency
    ax = axes[2]
    bars = ax.bar(labels, p50s, color=colors, width=0.6)
    ax.set_ylabel("p50 latency (ms)")
    ax.set_title("p50 latency (lower is better)")
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    ymax = max(p50s) * 1.25 if p50s else 1.0
    ax.set_ylim(0, ymax)
    for b, v in zip(bars, p50s, strict=True):
        ax.text(
            b.get_x() + b.get_width() / 2,
            v + ymax * 0.01,
            f"{v:.3f}",
            ha="center",
            va="bottom",
            fontsize=10,
            fontweight="bold",
        )

    fig.suptitle(
        f"Cross-Dataset Semantic Alignment Comparison "
        f"(liver PCA {payload['dim']}D, N={payload['n_total']}, "
        f"{payload['n_splits']}-way split, queries={payload['queries']})",
        fontsize=13,
        fontweight="bold",
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=int(dpi), bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    """脚本入口。"""
    args = parse_args()
    in_path = _resolve(args.in_path)
    out_path = _resolve(args.out)
    if not in_path.is_file():
        raise FileNotFoundError(f"输入 JSON 不存在: {in_path}")
    payload = json.loads(in_path.read_text())
    render(payload, out_path, dpi=int(args.dpi))
    print(f"输出 -> {out_path}")


if __name__ == "__main__":
    main()
