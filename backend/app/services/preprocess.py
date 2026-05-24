"""单细胞数据预处理服务。

读取 ``.h5ad`` 文件，提取或计算细胞向量（PCA 或 HVG-raw），并把向量、元信息、
2D 可视化坐标等结果落盘到指定目录，供后续 ANN 索引使用。

支持两种向量化策略（由 :func:`preprocess_h5ad` 的 ``vector_source`` 参数控制）：

    - ``pca`` (默认)
        复用 ``adata.obsm['X_pca']`` 或现场跑 ``scanpy`` 标准流水线
        （QC → normalize → log1p → HVG → scale → PCA(50)），向量以稠密
        ``float32`` 矩阵落盘 ``vectors.npy``，``vector_format='dense'``。
    - ``raw_sparse`` (M2.C5 扩展功能)
        跳过 PCA，对 ``adata`` 跑 ``normalize_total → log1p →
        highly_variable_genes`` 后 ``subset to top-5000 HVG``，把得到的
        :class:`scipy.sparse.csr_matrix` 直接落盘 ``vectors.npz``，
        ``vector_format='sparse'``。配合 :class:`SparseBruteBackend` 检索，
        保留稀有基因的强表达信号。

设计要点：
    - 对大文件友好：依赖 ``scanpy.read_h5ad`` 的稀疏路径，不重复 copy；
    - PCA 模式优先复用 ``adata.obsm['X_pca']``，避免重复算 PCA；
    - sparse 模式全程不展开成稠密，节省内存（5000 维 × 50k cells 稠密化
      就要 ~1GB，稀疏化通常 < 100MB）；
    - UMAP 优先使用已有 ``X_umap``，否则尝试 ``umap-learn``，再退化到
      sklearn ``TSNE``；sparse 模式下若 5000 维直接降维太慢，跳过 UMAP。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import numpy as np
from scipy import sparse as sp

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_DTYPE_MAP: dict[str, type[np.floating]] = {
    "float32": np.float32,
    "float16": np.float16,
}

VectorSource = Literal["pca", "raw_sparse"]


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
    """提取或计算细胞向量 (dense / PCA 模式)。

    顺序：
        1. 若 ``adata.obsm`` 中已有 ``X_pca``，直接复用；
        2. 否则跑 QC + Normalize + log1p + HVG + Scale + PCA；
        3. 兜底使用 ``adata.X``，稀疏矩阵需 ``toarray()``。

    Args:
        adata: AnnData 对象。

    Returns:
        tuple[np.ndarray, str]: ``(float32 向量矩阵, vector_source 标签)``。
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


def _extract_sparse_vectors(adata: Any, n_top_genes: int = 5000) -> tuple[sp.csr_matrix, str]:
    """提取 HVG 稀疏向量矩阵 (raw_sparse 模式)。

    流程（保持稀疏，不调用 ``sc.pp.scale`` 因其会展开成 dense）：

        1. ``filter_cells(min_genes=200)`` + ``filter_genes(min_cells=3)``
           过滤低质量；
        2. ``normalize_total(target_sum=1e4)`` 按行归一到固定 library size；
        3. ``log1p``：``log1p(0)=0`` 保持稀疏；
        4. ``highly_variable_genes(n_top_genes=n_top_genes)`` 选 HVG；
        5. ``adata[:, hvg_mask].copy()`` 取子集，仍为 CSR 矩阵；
        6. 转 :class:`scipy.sparse.csr_matrix` 并 ``astype(float32)``。

    Args:
        adata: AnnData 对象，要求 ``adata.X`` 为稀疏（CSR/CSC）。
        n_top_genes: HVG 数量上限，默认 ``5000``。

    Returns:
        tuple[csr_matrix, str]: ``(稀疏向量矩阵, vector_source 标签)``，
            标签固定为 ``"HVG_raw_sparse"``。

    Raises:
        RuntimeError: 走稀疏路径但 ``adata.X`` 为稠密，或 scanpy 流水线失败。
    """
    import scanpy as sc

    if not sp.issparse(adata.X):
        # 稠密 X 强行走稀疏路径会失去意义；明确拒绝
        raise RuntimeError(
            f"raw_sparse 模式要求 adata.X 为稀疏，实际 type={type(adata.X).__name__}"
        )

    sc.pp.filter_cells(adata, min_genes=200)
    sc.pp.filter_genes(adata, min_cells=3)
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)

    # HVG 选择需要的实际基因数不超过当前列数
    actual_top = min(int(n_top_genes), int(adata.shape[1]))
    sc.pp.highly_variable_genes(adata, n_top_genes=actual_top)
    adata_hvg = adata[:, adata.var.highly_variable].copy()

    x = adata_hvg.X
    if not sp.issparse(x):
        raise RuntimeError(f"HVG 子集后 X 已退化为稠密 type={type(x).__name__}")
    csr = x.tocsr() if not isinstance(x, sp.csr_matrix) else x
    if csr.dtype != np.float32:
        csr = csr.astype(np.float32, copy=False)
    logger.info(
        "raw_sparse 向量提取完成 shape=%s nnz=%d sparsity=%.2f%%",
        csr.shape,
        csr.nnz,
        100.0 * (1.0 - csr.nnz / (csr.shape[0] * max(csr.shape[1], 1))),
    )
    return csr, "HVG_raw_sparse"


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


def preprocess_h5ad(
    h5ad_path: str | Path,
    dataset_dir: str | Path,
    vector_source: VectorSource = "pca",
    n_top_genes: int = 5000,
) -> dict[str, Any]:
    """对 ``.h5ad`` 数据进行预处理并落盘。

    Args:
        h5ad_path: 原始 ``.h5ad`` 文件路径。
        dataset_dir: 预处理结果输出目录，不存在会自动创建。
        vector_source: 向量化策略：

            - ``pca`` (默认): 走 PCA 流水线，落盘 ``vectors.npy`` (dense)。
            - ``raw_sparse``: 走 HVG 稀疏流水线，落盘 ``vectors.npz`` (CSR)。

        n_top_genes: ``raw_sparse`` 模式下的 HVG 数量上限，默认 ``5000``；
            ``pca`` 模式忽略此参数（内部固定 2000）。

    Raises:
        FileNotFoundError: ``h5ad_path`` 指向的文件不存在。
        ValueError: ``vector_source`` 取值非法。
        RuntimeError: 读取或向量提取失败。

    Returns:
        dict: 包含以下字段——

            - ``vectors_path`` (str): 落盘的向量文件路径；
            - ``cell_count`` (int): 预处理后的细胞数；
            - ``vector_dim`` (int): 向量维度；
            - ``vector_source`` (str): 提取方式标签 (``X_pca`` / ``HVG_raw_sparse`` 等)；
            - ``vector_format`` (str): 存储格式 (``dense`` / ``sparse``)；
            - ``vector_dtype`` (str): 数据类型名 (``float32`` / ``float16``)；
            - ``meta_columns`` (list[str]): 元信息离散列；
            - ``umap_path`` (str | None): UMAP/TSNE 2D 坐标文件，可能为空。
    """
    if vector_source not in ("pca", "raw_sparse"):
        raise ValueError(f"vector_source 非法: {vector_source}; 取值 pca|raw_sparse")

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

    if vector_source == "raw_sparse":
        return _preprocess_raw_sparse(adata, out_dir, n_top_genes=n_top_genes)
    return _preprocess_pca(adata, out_dir)


def _preprocess_pca(adata: Any, out_dir: Path) -> dict[str, Any]:
    """走 PCA 流水线并落盘 dense ``vectors.npy``。

    Args:
        adata: 已加载的 AnnData 对象。
        out_dir: 输出目录。

    Returns:
        dict: 见 :func:`preprocess_h5ad` 返回值，``vector_format='dense'``。
    """
    try:
        vectors, vector_source_label = _extract_vectors(adata)
    except Exception as exc:
        raise RuntimeError(f"提取细胞向量失败: {exc}") from exc

    if vectors.dtype != np.float32:
        vectors = vectors.astype(np.float32, copy=False)

    dtype_key = (settings.VECTORS_DTYPE or "float32").lower()
    save_dtype = _DTYPE_MAP.get(dtype_key, np.float32)
    vectors_to_save = (
        vectors if save_dtype == np.float32 else vectors.astype(save_dtype, copy=False)
    )

    vectors_path = out_dir / "vectors.npy"
    np.save(vectors_path, vectors_to_save)

    meta_columns = _persist_metadata(adata, out_dir)
    umap_path: str | None = None
    umap_2d = _compute_umap_2d(adata, vectors)
    if umap_2d is not None:
        umap_file = out_dir / "umap_2d.npy"
        np.save(umap_file, umap_2d.astype(np.float32, copy=False))
        umap_path = str(umap_file)

    cell_count = int(vectors.shape[0])
    vector_dim = int(vectors.shape[1]) if vectors.ndim == 2 else 0
    logger.info(
        "PCA 预处理完成：cells=%d dim=%d source=%s umap=%s",
        cell_count,
        vector_dim,
        vector_source_label,
        umap_path,
    )
    return {
        "vectors_path": str(vectors_path),
        "cell_count": cell_count,
        "vector_dim": vector_dim,
        "vector_source": vector_source_label,
        "vector_format": "dense",
        "vector_dtype": str(np.dtype(save_dtype).name),
        "meta_columns": meta_columns,
        "umap_path": umap_path,
    }


def _preprocess_raw_sparse(adata: Any, out_dir: Path, n_top_genes: int = 5000) -> dict[str, Any]:
    """走 HVG 稀疏流水线并落盘 ``vectors.npz``。

    Args:
        adata: 已加载的 AnnData 对象。
        out_dir: 输出目录。
        n_top_genes: HVG 数量上限。

    Returns:
        dict: 见 :func:`preprocess_h5ad` 返回值，``vector_format='sparse'``。
    """
    try:
        csr, vector_source_label = _extract_sparse_vectors(adata, n_top_genes=n_top_genes)
    except Exception as exc:
        raise RuntimeError(f"提取稀疏向量失败: {exc}") from exc

    vectors_path = out_dir / "vectors.npz"
    sp.save_npz(vectors_path, csr)

    meta_columns = _persist_metadata(adata, out_dir)
    # sparse 模式下不强求 UMAP：5000 维直降太慢，留给后续按需补
    umap_path: str | None = None
    try:
        if "X_umap" in adata.obsm:
            umap_2d = np.asarray(adata.obsm["X_umap"], dtype=np.float32)
            umap_file = out_dir / "umap_2d.npy"
            np.save(umap_file, umap_2d)
            umap_path = str(umap_file)
    except Exception as exc:  # noqa: BLE001
        logger.warning("sparse 模式读取已有 X_umap 失败：%s", exc)

    cell_count = int(csr.shape[0])
    vector_dim = int(csr.shape[1])
    logger.info(
        "raw_sparse 预处理完成：cells=%d dim=%d nnz=%d source=%s",
        cell_count,
        vector_dim,
        csr.nnz,
        vector_source_label,
    )
    return {
        "vectors_path": str(vectors_path),
        "cell_count": cell_count,
        "vector_dim": vector_dim,
        "vector_source": vector_source_label,
        "vector_format": "sparse",
        "vector_dtype": "float32",
        "meta_columns": meta_columns,
        "umap_path": umap_path,
    }


def _persist_metadata(adata: Any, out_dir: Path) -> list[str]:
    """落盘 ``cell_ids.json`` 与 ``metadata.parquet``/``csv``，返回离散列名列表。"""
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

    return compute_meta_columns(adata)
