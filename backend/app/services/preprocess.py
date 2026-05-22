"""单细胞数据预处理服务。

读取 ``.h5ad`` 文件，提取或计算细胞向量（PCA 等），并把向量、元信息、
2D 可视化坐标等结果落盘到指定目录，供后续 ANN 索引使用。

设计要点：
    - 对大文件友好：依赖 ``scanpy.read_h5ad`` 的稀疏路径，不重复 copy；
    - 优先复用 ``adata.obsm['X_pca']``，避免重复算 PCA；
    - 兜底使用原始 ``adata.X``，但稀疏矩阵需 ``toarray()``；
    - UMAP 优先使用已有 ``X_umap``，否则尝试 ``umap-learn``，再退化到 sklearn ``TSNE``；
    - 所有 IO/计算阶段都包了 try/except，失败抛清晰异常。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from app.core.logging import get_logger

logger = get_logger(__name__)


def compute_meta_columns(adata: Any, max_columns: int = 8) -> list[str]:
    """从 ``adata.obs`` 中筛选可作为过滤条件的离散字段。

    选取规则：
        - ``category`` / ``object`` / ``bool`` 列直接保留；
        - 数值列若去重后基数 ``<= 50``，视为离散列保留；
        - 按列顺序最多返回 ``max_columns`` 个，避免前端展示过载。

    Args:
        adata: AnnData 对象。
        max_columns: 返回的最大列数。

    Returns:
        list[str]: 选中的列名列表。
    """
    obs = adata.obs
    selected: list[str] = []
    for col in obs.columns:
        series = obs[col]
        dtype_name = str(series.dtype)
        is_discrete = False
        if dtype_name == "category" or dtype_name == "object" or dtype_name == "bool":
            is_discrete = True
        else:
            try:
                if series.nunique(dropna=True) <= 50:
                    is_discrete = True
            except TypeError:
                is_discrete = False
        if is_discrete:
            selected.append(str(col))
            if len(selected) >= max_columns:
                break
    return selected


def _extract_vectors(adata: Any) -> tuple[np.ndarray, str]:
    """提取或计算细胞向量。

    顺序：
        1. 若 ``adata.obsm`` 中已有 ``X_pca``，直接复用；
        2. 否则跑 QC + Normalize + log1p + HVG + Scale + PCA；
        3. 兜底使用 ``adata.X``，稀疏矩阵需 ``toarray()``。

    Args:
        adata: AnnData 对象。

    Returns:
        tuple[np.ndarray, str]: ``(float32 向量矩阵, vector_source)``。
    """
    if "X_pca" in adata.obsm:
        logger.info("复用已有 X_pca 作为细胞向量")
        return np.asarray(adata.obsm["X_pca"], dtype=np.float32), "X_pca"

    try:
        import scanpy as sc

        sc.pp.filter_cells(adata, min_genes=200)
        sc.pp.filter_genes(adata, min_cells=3)
        sc.pp.normalize_total(adata, target_sum=1e4)
        sc.pp.log1p(adata)
        sc.pp.highly_variable_genes(adata, n_top_genes=2000)
        adata_hvg = adata[:, adata.var.highly_variable].copy()
        sc.pp.scale(adata_hvg, max_value=10)
        sc.tl.pca(adata_hvg, n_comps=50)
        vectors = np.asarray(adata_hvg.obsm["X_pca"], dtype=np.float32)
        logger.info("PCA 计算完成，shape=%s", vectors.shape)
        return vectors, "X_pca"
    except Exception as exc:  # noqa: BLE001
        logger.warning("PCA 计算失败，退化使用 adata.X：%s", exc)
        x = adata.X
        if hasattr(x, "toarray"):
            vectors = x.toarray().astype(np.float32)
        else:
            vectors = np.asarray(x, dtype=np.float32)
        return vectors, "X"


def _compute_umap_2d(adata: Any, vectors: np.ndarray) -> np.ndarray | None:
    """获取 2D 可视化坐标。

    优先复用 ``adata.obsm['X_umap']``；没有时尝试 ``umap-learn``，最后退化到
    sklearn ``TSNE``。任何环节失败都返回 ``None``，调用方应容错。

    Args:
        adata: AnnData 对象。
        vectors: 已计算好的细胞向量，作为降维输入。

    Returns:
        np.ndarray | None: 形如 ``(N, 2)`` 的 float32 数组，失败时为 ``None``。
    """
    try:
        if "X_umap" in adata.obsm:
            return np.asarray(adata.obsm["X_umap"], dtype=np.float32)
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取已有 X_umap 失败：%s", exc)

    try:
        import umap as umap_module

        reducer = umap_module.UMAP(n_components=2, random_state=42)
        return np.asarray(reducer.fit_transform(vectors), dtype=np.float32)
    except Exception as exc:  # noqa: BLE001
        logger.info("umap-learn 不可用或失败，回退 TSNE：%s", exc)

    try:
        from sklearn.manifold import TSNE

        tsne = TSNE(
            n_components=2,
            random_state=42,
            init="random",
            learning_rate="auto",
        )
        return np.asarray(tsne.fit_transform(vectors), dtype=np.float32)
    except Exception as exc:  # noqa: BLE001
        logger.warning("TSNE 降维失败：%s", exc)
        return None


def preprocess_h5ad(h5ad_path: str | Path, dataset_dir: str | Path) -> dict[str, Any]:
    """对 ``.h5ad`` 数据进行预处理并落盘。

    流程：
        1. ``scanpy.read_h5ad`` 读取数据；
        2. 优先复用 ``X_pca``，否则跑 QC + PCA，再兜底 ``adata.X``；
        3. 向量保存为 ``{dataset_dir}/vectors.npy``（float32）；
        4. 同步保存 ``cell_ids.json`` 与 ``metadata.parquet``（失败回退 csv）；
        5. 若存在 ``X_umap`` 直接落盘，否则尝试 umap-learn / TSNE。

    Args:
        h5ad_path: 原始 ``.h5ad`` 文件路径。
        dataset_dir: 预处理结果输出目录，不存在会自动创建。

    Raises:
        FileNotFoundError: ``h5ad_path`` 指向的文件不存在。
        RuntimeError: 读取或向量提取失败。

    Returns:
        dict: 包含 ``vectors_path``、``cell_count``、``vector_dim``、
            ``vector_source``、``meta_columns``、``umap_path`` 等字段。
    """
    h5ad_p = Path(h5ad_path)
    out_dir = Path(dataset_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not h5ad_p.exists():
        raise FileNotFoundError(f"h5ad 文件不存在: {h5ad_p}")

    try:
        import scanpy as sc

        adata = sc.read_h5ad(str(h5ad_p))
    except Exception as exc:
        raise RuntimeError(f"读取 h5ad 失败: {exc}") from exc

    try:
        vectors, vector_source = _extract_vectors(adata)
    except Exception as exc:
        raise RuntimeError(f"提取细胞向量失败: {exc}") from exc

    if vectors.dtype != np.float32:
        vectors = vectors.astype(np.float32, copy=False)

    vectors_path = out_dir / "vectors.npy"
    np.save(vectors_path, vectors)

    cell_ids = [str(cid) for cid in adata.obs_names]
    cell_ids_path = out_dir / "cell_ids.json"
    with cell_ids_path.open("w", encoding="utf-8") as f:
        json.dump(cell_ids, f, ensure_ascii=False)

    metadata_path: Path = out_dir / "metadata.parquet"
    try:
        adata.obs.to_parquet(metadata_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("写 metadata.parquet 失败，回退 csv：%s", exc)
        metadata_path = out_dir / "metadata.csv"
        adata.obs.to_csv(metadata_path)

    meta_columns = compute_meta_columns(adata)

    umap_path: str | None = None
    umap_2d = _compute_umap_2d(adata, vectors)
    if umap_2d is not None:
        umap_file = out_dir / "umap_2d.npy"
        np.save(umap_file, umap_2d.astype(np.float32, copy=False))
        umap_path = str(umap_file)

    cell_count = int(vectors.shape[0])
    vector_dim = int(vectors.shape[1]) if vectors.ndim == 2 else 0

    logger.info(
        "预处理完成：cells=%d dim=%d source=%s umap=%s",
        cell_count,
        vector_dim,
        vector_source,
        umap_path,
    )

    return {
        "vectors_path": str(vectors_path),
        "cell_count": cell_count,
        "vector_dim": vector_dim,
        "vector_source": vector_source,
        "meta_columns": meta_columns,
        "umap_path": umap_path,
    }
