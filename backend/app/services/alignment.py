"""跨数据集语义对齐服务 (v1.2 D7 加分项)。

把多个 :class:`app.models.dataset.Dataset` 的细胞统一到同一向量空间，
支持两种策略：

    - ``intersect_only`` (默认)
        读取每个 dataset 的 h5ad，取基因集交集，在统一 gene 空间上重新跑
        ``normalize_total + log1p + scale + PCA(target_dim)``，让所有
        细胞共享同一组 PCA 主成分。
    - ``harmony``
        intersect 之后，把 PCA 矩阵交给 :mod:`harmonypy` 做 batch
        correction，移除不同实验来源的批次效应。``harmonypy`` 未安装时
        优雅降级为 ``intersect_only`` 并在 :class:`AlignedDataset.method`
        如实回填。

落盘约定：

    - ``{DATA_DIR}/aligned/{aligned_id}/vectors.npy``
        ``(cell_count_total, target_dim)`` 的 float32 矩阵，行序与
        cell_map.json 一致；
    - ``{DATA_DIR}/aligned/{aligned_id}/cell_ids.json``
        长度为 ``cell_count_total`` 的 ``list[str]``；
    - ``{DATA_DIR}/aligned/{aligned_id}/cell_map.json``
        ``[{"cell_id": ..., "source_dataset_id": ...}, ...]``，行序与
        ``cell_ids.json`` / ``vectors.npy`` 一致；
    - ``{DATA_DIR}/aligned/{aligned_id}/metadata.parquet`` (可选)
        合并所有 dataset 的 obs（按列做并集）+ 新增 ``source_dataset_id``
        列；缺失字段填 ``None``，写盘失败时回退到 ``metadata.csv``。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from scipy import sparse as sp
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.aligned_dataset import AlignedDataset
from app.models.dataset import Dataset

logger = get_logger(__name__)

AlignMethod = Literal["intersect_only", "harmony"]


def build_aligned_dir(aligned_id: int) -> Path:
    """计算对齐数据集的落盘目录。

    Args:
        aligned_id: 对齐数据集 ID。

    Returns:
        Path: ``{DATA_DIR}/aligned/{aligned_id}``，目录不存在会自动创建。
    """
    out_dir = Path(settings.DATA_DIR) / "aligned" / str(aligned_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _has_harmonypy() -> bool:
    """探测 harmonypy 是否可用。

    Returns:
        bool: 可成功 import 返回 ``True``。
    """
    try:
        import harmonypy  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def _read_h5ad_minimal(h5ad_path: str) -> Any:
    """读取 .h5ad 文件，复用 scanpy。

    Args:
        h5ad_path: 文件路径。

    Returns:
        AnnData: 已加载的 AnnData 对象。

    Raises:
        RuntimeError: 文件读取失败。
    """
    try:
        import scanpy as sc

        return sc.read_h5ad(h5ad_path)
    except Exception as exc:
        raise RuntimeError(f"读取 h5ad 失败 path={h5ad_path}: {exc}") from exc


def _intersect_genes(adatas: list[Any]) -> list[str]:
    """计算多个 adata 的基因集交集，保持稳定顺序（按首个 adata 的 var_names）。

    Args:
        adatas: AnnData 对象列表。

    Returns:
        list[str]: 交集基因名列表，保持 ``adatas[0].var_names`` 中的相对顺序。
    """
    if not adatas:
        return []
    first = [str(g) for g in adatas[0].var_names]
    common: set[str] = set(first)
    for ad in adatas[1:]:
        common &= {str(g) for g in ad.var_names}
    return [g for g in first if g in common]


def _build_gene_matrix(adata: Any, gene_list: list[str]) -> np.ndarray:
    """从 adata 中按 gene_list 顺序提取表达矩阵。

    优先从 ``adata.X`` 取，按需 toarray()，最后转为 float32 dense 矩阵。

    Args:
        adata: AnnData 对象。
        gene_list: 目标基因列表，按该顺序提取列。

    Returns:
        np.ndarray: ``(n_cells, len(gene_list))`` float32 dense 矩阵。

    Raises:
        ValueError: 当 adata 中缺少 gene_list 中的基因时抛出。
    """
    var_names = list(map(str, adata.var_names))
    var_idx = {g: i for i, g in enumerate(var_names)}
    missing = [g for g in gene_list if g not in var_idx]
    if missing:
        raise ValueError(f"adata 缺少基因 {missing[:5]} ...，共 {len(missing)} 个")
    cols = np.fromiter((var_idx[g] for g in gene_list), dtype=np.int64, count=len(gene_list))

    x = adata.X
    if sp.issparse(x):
        sub = x[:, cols]
        dense = sub.toarray() if hasattr(sub, "toarray") else np.asarray(sub)
    else:
        dense = np.asarray(x)[:, cols]
    return dense.astype(np.float32, copy=False)


def _normalize_log1p(matrix: np.ndarray) -> np.ndarray:
    """对 dense 矩阵执行按行 ``normalize_total(target=1e4) + log1p``。

    与 scanpy 同效，但避免再走稀疏路径，节省转换成本。

    Args:
        matrix: ``(n_cells, n_genes)`` 的 float32 dense 矩阵。

    Returns:
        np.ndarray: 同形状的归一化 + log1p 后矩阵。
    """
    row_sum = matrix.sum(axis=1, keepdims=True)
    row_sum[row_sum == 0.0] = 1.0  # 避免除零
    scaled = matrix * (1e4 / row_sum)
    return np.log1p(scaled).astype(np.float32, copy=False)


def _scale_columns(matrix: np.ndarray, max_value: float = 10.0) -> np.ndarray:
    """按列做 z-score 标准化，并裁剪 ``[-max_value, max_value]``。

    与 ``scanpy.pp.scale`` 同效。

    Args:
        matrix: ``(n_cells, n_genes)`` float32 矩阵。
        max_value: 裁剪上下界。

    Returns:
        np.ndarray: 标准化后的矩阵。
    """
    mean = matrix.mean(axis=0, keepdims=True)
    std = matrix.std(axis=0, keepdims=True)
    std[std == 0.0] = 1.0
    out = (matrix - mean) / std
    np.clip(out, -max_value, max_value, out=out)
    return out.astype(np.float32, copy=False)


def _fit_pca(matrix: np.ndarray, target_dim: int) -> np.ndarray:
    """跑 PCA，把 ``(N, G)`` 投到 ``(N, target_dim)``。

    实际 PCA 维度受 ``min(N, G, target_dim)`` 限制，遇到极小数据集会自动缩小。

    Args:
        matrix: ``(N, G)`` float32 矩阵。
        target_dim: 期望维度。

    Returns:
        np.ndarray: ``(N, k)`` 投影，``k <= target_dim``。
    """
    n_samples, n_features = matrix.shape
    k = max(1, min(int(target_dim), int(n_samples), int(n_features)))

    try:
        from sklearn.decomposition import PCA

        pca = PCA(n_components=k, random_state=42, svd_solver="auto")
        projected = pca.fit_transform(matrix)
        return np.asarray(projected, dtype=np.float32)
    except Exception as exc:  # noqa: BLE001
        logger.warning("sklearn PCA 失败，退化到 SVD: %s", exc)
        # 兜底：直接 SVD
        mean = matrix.mean(axis=0, keepdims=True)
        centered = matrix - mean
        _, _, vt = np.linalg.svd(centered, full_matrices=False)
        components = vt[:k]
        projected = centered @ components.T
        return projected.astype(np.float32, copy=False)


def _apply_harmony(pca_matrix: np.ndarray, batch_labels: list[int]) -> np.ndarray:
    """调用 harmonypy 做 batch correction。

    Args:
        pca_matrix: ``(N, k)`` PCA 投影。
        batch_labels: 长度 ``N`` 的 batch 标签列表（这里用 source_dataset_id）。

    Returns:
        np.ndarray: harmony 校正后的同形状矩阵。

    Raises:
        RuntimeError: harmonypy 调用失败时抛出（外层会兜底为 intersect_only）。
    """
    import harmonypy as hm

    meta_df = pd.DataFrame({"batch": [str(b) for b in batch_labels]})
    ho = hm.run_harmony(pca_matrix, meta_df, vars_use="batch", max_iter_harmony=10)
    return np.asarray(ho.Z_corr.T, dtype=np.float32)


def _persist_aligned_metadata(
    adatas: list[Any], source_dataset_ids: list[int], out_dir: Path
) -> list[str]:
    """合并所有 adata 的 obs 并落盘 ``metadata.parquet`` / ``cell_ids.json``。

    Args:
        adatas: 与 source_dataset_ids 一一对应的 AnnData 列表。
        source_dataset_ids: 与 adatas 对齐的 dataset ID 列表。
        out_dir: 输出目录。

    Returns:
        list[str]: 写盘的 cell_ids 列表（行序与 vectors 一致）。
    """
    cell_ids: list[str] = []
    frames: list[pd.DataFrame] = []
    for ad, ds_id in zip(adatas, source_dataset_ids, strict=False):
        ids = [str(c) for c in ad.obs_names]
        cell_ids.extend(ids)
        df = ad.obs.copy()
        df = df.reset_index(drop=True)
        df["source_dataset_id"] = int(ds_id)
        df["cell_id"] = ids
        frames.append(df)

    merged = pd.concat(frames, axis=0, ignore_index=True, sort=False)

    cell_ids_path = out_dir / "cell_ids.json"
    with cell_ids_path.open("w", encoding="utf-8") as f:
        json.dump(cell_ids, f, ensure_ascii=False)

    cell_map = [
        {"cell_id": cid, "source_dataset_id": int(sid)}
        for cid, sid in zip(
            cell_ids,
            np.concatenate(
                [
                    np.full(len(ad.obs_names), ds_id, dtype=np.int64)
                    for ad, ds_id in zip(adatas, source_dataset_ids, strict=False)
                ]
            ).tolist(),
            strict=False,
        )
    ]
    cell_map_path = out_dir / "cell_map.json"
    with cell_map_path.open("w", encoding="utf-8") as f:
        json.dump(cell_map, f, ensure_ascii=False)

    metadata_path: Path = out_dir / "metadata.parquet"
    try:
        merged.to_parquet(metadata_path, index=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("写 metadata.parquet 失败，回退 csv: %s", exc)
        metadata_path = out_dir / "metadata.csv"
        merged.to_csv(metadata_path, index=False)

    return cell_ids


def _default_aligned_name(method: str, dataset_ids: list[int]) -> str:
    """根据 method + dataset_ids 生成默认对齐名称。

    Args:
        method: 对齐方法。
        dataset_ids: 原始 dataset ID 列表。

    Returns:
        str: 例如 ``"aligned-intersect_only-[1,2,3]"``。
    """
    ids_str = "[" + ",".join(str(i) for i in dataset_ids) + "]"
    return f"aligned-{method}-{ids_str}"


async def align_datasets(
    session: AsyncSession,
    dataset_ids: list[int],
    method: AlignMethod = "intersect_only",
    target_dim: int = 30,
    user_id: int | None = None,
    name: str | None = None,
) -> int:
    """对多个数据集做基因集对齐 + 可选 batch correction，落库 AlignedDataset。

    流程：
        1. 校验 ``dataset_ids`` 长度 >= 2，原始 dataset 均存在；
        2. 创建 ``status=running`` 的 :class:`AlignedDataset` 记录获取 ID；
        3. 加载所有 dataset 的 h5ad 文件；
        4. ``intersect_only``: 取基因集交集，统一 normalize + log1p +
           scale + PCA(target_dim)；
        5. ``harmony``: intersect 后调用 harmonypy 做 batch correction；
           harmonypy 缺失或调用失败时降级为 ``intersect_only``；
        6. 落盘 ``vectors.npy`` + ``cell_ids.json`` + ``cell_map.json``
           + ``metadata.parquet``；
        7. 更新 :class:`AlignedDataset` 字段并置 ``status=done``。

    Args:
        session: 异步数据库会话。
        dataset_ids: 参与对齐的原始 dataset ID 列表，长度需 >= 2。
        method: 对齐方法。harmonypy 不可用时自动降级。
        target_dim: 对齐后向量维度。
        user_id: 触发对齐的用户 ID，可空。
        name: 对齐数据集名称；不传时自动生成。

    Returns:
        int: 新建的 :class:`AlignedDataset` ID。

    Raises:
        ValueError: ``dataset_ids`` 长度 < 2，或包含不存在的 ID。
        RuntimeError: 对齐流程中向量加载 / PCA / 落盘失败。
    """
    if len(dataset_ids) < 2:
        raise ValueError(f"对齐至少需要 2 个数据集，当前 {len(dataset_ids)}")

    # 1. 校验所有 dataset 存在
    datasets: list[Dataset] = []
    for ds_id in dataset_ids:
        ds = await session.get(Dataset, ds_id)
        if ds is None:
            raise ValueError(f"数据集不存在: {ds_id}")
        datasets.append(ds)

    # 2. 创建 pending 记录
    record_name = name or _default_aligned_name(method, dataset_ids)
    aligned = AlignedDataset(
        name=record_name,
        source_dataset_ids_json=json.dumps(dataset_ids),
        method=method,
        target_dim=int(target_dim),
        cell_count=0,
        common_genes_count=0,
        status="running",
        created_by=user_id,
    )
    session.add(aligned)
    await session.commit()
    await session.refresh(aligned)

    try:
        # 3. 加载所有 h5ad
        adatas = [_read_h5ad_minimal(ds.h5ad_path) for ds in datasets]

        # 4. 基因交集
        common_genes = _intersect_genes(adatas)
        if not common_genes:
            raise RuntimeError("所有数据集的基因集交集为空，无法对齐")

        # 5. 拼接 + 归一化 + PCA
        per_matrix = [_build_gene_matrix(ad, common_genes) for ad in adatas]
        stacked = np.concatenate(per_matrix, axis=0)  # (N_total, G)
        normalized = _normalize_log1p(stacked)
        scaled = _scale_columns(normalized)
        projected = _fit_pca(scaled, target_dim=target_dim)
        actual_dim = int(projected.shape[1])

        # 6. 可选 harmony
        effective_method: str = method
        if method == "harmony":
            if not _has_harmonypy():
                logger.warning("harmonypy 未安装，降级为 intersect_only")
                effective_method = "intersect_only"
            else:
                batch_labels: list[int] = []
                for ds_id, mat in zip(dataset_ids, per_matrix, strict=False):
                    batch_labels.extend([int(ds_id)] * mat.shape[0])
                try:
                    projected = _apply_harmony(projected, batch_labels)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("harmony 校正失败，降级为 intersect_only: %s", exc)
                    effective_method = "intersect_only"

        # 7. 落盘
        out_dir = build_aligned_dir(int(aligned.id))
        vectors_path = out_dir / "vectors.npy"
        np.save(vectors_path, projected.astype(np.float32, copy=False))
        _persist_aligned_metadata(adatas, dataset_ids, out_dir)
        cell_map_path = out_dir / "cell_map.json"

        # 8. 更新记录
        aligned.method = effective_method
        aligned.target_dim = actual_dim
        aligned.cell_count = int(projected.shape[0])
        aligned.common_genes_count = int(len(common_genes))
        aligned.vectors_path = str(vectors_path)
        aligned.cell_map_path = str(cell_map_path)
        aligned.status = "done"
        await session.commit()
        await session.refresh(aligned)

        logger.info(
            "对齐完成 aligned_id=%s method=%s cells=%d genes=%d dim=%d",
            aligned.id,
            effective_method,
            aligned.cell_count,
            aligned.common_genes_count,
            actual_dim,
        )
        return int(aligned.id)
    except Exception as exc:
        aligned.status = "failed"
        await session.commit()
        logger.exception("对齐失败 aligned_id=%s err=%s", aligned.id, exc)
        raise


def cleanup_aligned_files(aligned: AlignedDataset) -> None:
    """清理对齐数据集对应的磁盘文件（向量、cell map、metadata 等整个目录）。

    单一文件 / 目录失败仅记录日志，不抛出，避免阻断 DB 删除。

    Args:
        aligned: 待清理的对齐数据集对象。
    """
    out_dir = Path(settings.DATA_DIR) / "aligned" / str(aligned.id)
    if out_dir.exists():
        import shutil

        try:
            shutil.rmtree(out_dir, ignore_errors=True)
        except OSError as exc:
            logger.warning("删除对齐目录失败 path=%s err=%s", out_dir, exc)


def load_aligned_artifacts(aligned: AlignedDataset) -> dict[str, Any]:
    """加载对齐数据集的预处理制品（vectors / cell_ids / metadata / cell_map）。

    Args:
        aligned: 对齐数据集 ORM 对象。

    Returns:
        dict[str, Any]: ``{"vectors": np.ndarray, "cell_ids": list[str],
            "metadata": pd.DataFrame, "cell_id_to_index": dict[str, int],
            "cell_map": list[dict]}``。

    Raises:
        RuntimeError: 当 vectors / cell_ids / cell_map 任一文件缺失。
    """
    if not aligned.vectors_path or not Path(aligned.vectors_path).is_file():
        raise RuntimeError(f"对齐数据集 {aligned.id} 缺少 vectors.npy")
    if not aligned.cell_map_path or not Path(aligned.cell_map_path).is_file():
        raise RuntimeError(f"对齐数据集 {aligned.id} 缺少 cell_map.json")

    out_dir = Path(aligned.vectors_path).parent
    vectors = np.load(aligned.vectors_path).astype(np.float32, copy=False)

    cell_ids_path = out_dir / "cell_ids.json"
    if not cell_ids_path.is_file():
        raise RuntimeError(f"对齐数据集 {aligned.id} 缺少 cell_ids.json")
    with cell_ids_path.open(encoding="utf-8") as f:
        cell_ids = [str(c) for c in json.load(f)]

    with Path(aligned.cell_map_path).open(encoding="utf-8") as f:
        cell_map = json.load(f)

    metadata: pd.DataFrame = pd.DataFrame()
    parquet_path = out_dir / "metadata.parquet"
    csv_path = out_dir / "metadata.csv"
    if parquet_path.is_file():
        try:
            metadata = pd.read_parquet(parquet_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("读取 metadata.parquet 失败: %s", exc)
    if metadata.empty and csv_path.is_file():
        metadata = pd.read_csv(csv_path)

    return {
        "vectors": vectors,
        "cell_ids": cell_ids,
        "metadata": metadata,
        "cell_id_to_index": {cid: i for i, cid in enumerate(cell_ids)},
        "cell_map": cell_map,
    }
