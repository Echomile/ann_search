"""数据集业务服务层。

把 :class:`app.models.dataset.Dataset` 相关的 DB / 磁盘逻辑从 HTTP 层剥离，
便于复用与单元测试。

约定：
    - 数据集所有权由调用方校验，本模块不负责权限判断；
    - 删除操作会尽力清理磁盘文件，但不会因 IO 异常阻断主流程；
    - 流式写入分块大小 ``CHUNK_SIZE`` 设为 8 MB，适配 GB 级 .h5ad。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.dataset import Dataset

logger = get_logger(__name__)

CHUNK_SIZE = 8 * 1024 * 1024
UMAP_MAX_SAMPLE = 50_000
UMAP_SAMPLE_SEED = 42

_UPLOAD_PROGRESS: dict[int, dict[str, int | None]] = {}
_UPLOAD_PROGRESS_LOCK = threading.Lock()


def set_upload_progress(
    dataset_id: int,
    bytes_received: int,
    total_bytes: int | None,
) -> None:
    """记录指定数据集的写盘进度（线程安全）。

    用于 ``GET /datasets/{id}/upload-progress`` 暴露后端 8 MB 分块写盘的真实进度，
    区别于浏览器侧 ``axios.onUploadProgress`` 看到的"字节进入网络"进度。
    记录仅保存在进程内存，多 worker 部署时需切换 Redis；当前业务量进程内字典足够。

    Args:
        dataset_id: 数据集 ID。
        bytes_received: 截至目前已写入磁盘的字节数。
        total_bytes: 上传文件总字节数；``starlette`` 流式上传可能为 ``None``。
    """
    with _UPLOAD_PROGRESS_LOCK:
        _UPLOAD_PROGRESS[dataset_id] = {
            "bytes_received": bytes_received,
            "total_bytes": total_bytes,
        }


def get_upload_progress(dataset_id: int) -> dict[str, int | None] | None:
    """读取指定数据集的写盘进度。

    Args:
        dataset_id: 数据集 ID。

    Returns:
        dict | None: ``{"bytes_received": int, "total_bytes": int | None}``；
        若该数据集不在上传中（已完成 / 失败 / 从未上传）返回 ``None``。
    """
    with _UPLOAD_PROGRESS_LOCK:
        record = _UPLOAD_PROGRESS.get(dataset_id)
        return dict(record) if record else None


def clear_upload_progress(dataset_id: int) -> None:
    """清理指定数据集的写盘进度记录。

    上传完成或失败时调用，避免内存泄漏。

    Args:
        dataset_id: 数据集 ID。
    """
    with _UPLOAD_PROGRESS_LOCK:
        _UPLOAD_PROGRESS.pop(dataset_id, None)


def build_raw_path(user_id: int) -> Path:
    """计算指定用户的原始 ``.h5ad`` 落盘目录。

    Args:
        user_id: 拥有者用户 ID。

    Returns:
        Path: ``{DATA_DIR}/raw/{user_id}``，目录不存在会自动创建。
    """
    raw_dir = Path(settings.DATA_DIR) / "raw" / str(user_id)
    raw_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir


def gen_h5ad_filename() -> str:
    """生成一个不重复的 .h5ad 文件名。

    Returns:
        str: ``{uuid4().hex}.h5ad`` 形式的文件名。
    """
    return f"{uuid.uuid4().hex}.h5ad"


async def stream_to_disk(
    reader: BinaryIO | object,
    dest: Path,
    *,
    on_chunk: Callable[[int], None] | None = None,
) -> int:
    """把上传的二进制流分块写入磁盘，返回写入字节数。

    ``reader`` 需支持异步 ``read(size)``（FastAPI ``UploadFile`` 即如此），
    分块大小受 :data:`CHUNK_SIZE` 控制，避免 OOM。

    Args:
        reader: 提供 ``async read(size)`` 的对象。
        dest: 目标文件路径。
        on_chunk: 可选回调，每写完一个 chunk 后以已写字节数为参数调用一次，
            供上层把真实写盘进度上报到 :func:`set_upload_progress`。

    Returns:
        int: 实际写入的字节数。

    Raises:
        Exception: IO 异常会向上抛出，调用方负责清理半完成的文件。
    """
    total = 0
    with dest.open("wb") as out:
        while True:
            chunk = await reader.read(CHUNK_SIZE)  # type: ignore[attr-defined]
            if not chunk:
                break
            out.write(chunk)
            total += len(chunk)
            if on_chunk is not None:
                on_chunk(total)
    return total


async def name_exists(db: AsyncSession, *, owner_id: int, name: str) -> bool:
    """检查指定用户名下是否已存在同名数据集。

    Args:
        db: 异步数据库会话。
        owner_id: 拥有者用户 ID。
        name: 数据集名称（区分大小写、不做 trim）。

    Returns:
        bool: 存在返回 ``True``，否则 ``False``。
    """
    stmt = select(Dataset.id).where(Dataset.owner_id == owner_id, Dataset.name == name).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def create_dataset(
    db: AsyncSession,
    *,
    owner_id: int,
    name: str,
    h5ad_path: str,
) -> Dataset:
    """新建一条数据集记录，初始状态 ``uploading``。

    Args:
        db: 异步数据库会话。
        owner_id: 拥有者用户 ID。
        name: 数据集名称。
        h5ad_path: 原始 .h5ad 文件落盘路径。

    Returns:
        Dataset: 已 refresh 的数据集对象。
    """
    ds = Dataset(
        owner_id=owner_id,
        name=name,
        h5ad_path=h5ad_path,
        status="uploading",
    )
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    return ds


async def list_user_datasets(db: AsyncSession, owner_id: int) -> list[Dataset]:
    """列出指定用户的数据集，按 ``created_at`` 倒序。

    Args:
        db: 异步数据库会话。
        owner_id: 拥有者用户 ID。

    Returns:
        list[Dataset]: 数据集列表。
    """
    stmt = (
        select(Dataset)
        .where(Dataset.owner_id == owner_id)
        .order_by(Dataset.created_at.desc(), Dataset.id.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_dataset(db: AsyncSession, dataset_id: int) -> Dataset | None:
    """按主键获取数据集。

    Args:
        db: 异步数据库会话。
        dataset_id: 数据集 ID。

    Returns:
        Dataset | None: 命中则返回对象，未命中返回 ``None``。
    """
    return await db.get(Dataset, dataset_id)


def cleanup_dataset_files(dataset: Dataset) -> None:
    """清理数据集对应的磁盘文件（原始、向量、索引、处理目录）。

    任何单一文件/目录删除失败都只记录日志，不抛出，避免阻断 DB 删除。

    Args:
        dataset: 待清理的数据集对象（属性必须已加载）。
    """
    candidates: list[Path] = []
    if dataset.h5ad_path:
        candidates.append(Path(dataset.h5ad_path))
    if dataset.vectors_path:
        candidates.append(Path(dataset.vectors_path))

    for p in candidates:
        try:
            if p.is_file():
                p.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("删除文件失败 path=%s err=%s", p, exc)

    dirs = [
        Path(settings.PROCESSED_DIR) / str(dataset.id),
        Path(settings.INDEX_DIR) / str(dataset.id),
    ]
    for d in dirs:
        try:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        except OSError as exc:
            logger.warning("删除目录失败 path=%s err=%s", d, exc)


async def delete_dataset(db: AsyncSession, dataset: Dataset) -> None:
    """删除数据集 DB 记录并清理磁盘。

    Args:
        db: 异步数据库会话。
        dataset: 要删除的数据集对象。
    """
    cleanup_dataset_files(dataset)
    await db.delete(dataset)
    await db.commit()


def _is_orphan(dataset: Dataset) -> bool:
    """判断单个数据集是否为孤儿。

    判定规则：
        - ``status='failed'``；或
        - ``status='ready'`` 但 ``vectors_path`` 为空或对应文件已丢失。

    Args:
        dataset: 待判定的数据集。

    Returns:
        bool: 是孤儿返回 ``True``，否则 ``False``。
    """
    if dataset.status == "failed":
        return True
    if dataset.status == "ready":
        vp = dataset.vectors_path
        if not vp or not os.path.exists(vp):
            return True
    return False


async def find_orphan_datasets(db: AsyncSession, owner_id: int) -> list[Dataset]:
    """查找指定用户名下的孤儿数据集。

    Args:
        db: 异步数据库会话。
        owner_id: 拥有者用户 ID。

    Returns:
        list[Dataset]: 满足孤儿条件的数据集列表。
    """
    stmt = select(Dataset).where(Dataset.owner_id == owner_id)
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    return [ds for ds in rows if _is_orphan(ds)]


async def cleanup_orphan_datasets(db: AsyncSession, owner_id: int) -> list[int]:
    """批量清理孤儿数据集（含磁盘文件），返回被清理的 ID 列表。

    单个清理失败仅记录日志，不影响后续条目继续处理。

    Args:
        db: 异步数据库会话。
        owner_id: 拥有者用户 ID。

    Returns:
        list[int]: 成功清理的数据集 ID 列表。
    """
    orphans = await find_orphan_datasets(db, owner_id)
    deleted: list[int] = []
    for ds in orphans:
        ds_id = ds.id
        try:
            await delete_dataset(db, ds)
        except Exception as exc:  # noqa: BLE001
            logger.warning("清理孤儿数据集失败 id=%s err=%s", ds_id, exc)
            continue
        deleted.append(ds_id)
    return deleted


def load_umap_2d(
    dataset_id: int,
    *,
    max_sample: int = UMAP_MAX_SAMPLE,
    seed: int = UMAP_SAMPLE_SEED,
) -> tuple[list[list[float]] | None, list[str] | None, bool, int]:
    """读取数据集预处理生成的 UMAP 2D 坐标与对齐的 cell_ids。

    超过 ``max_sample`` 时使用固定种子 ``RandomState(seed)`` 等概率下采样，
    避免 50 万级数据集把前端浏览器拖崩；下采样后 coords 与 cell_ids 索引保持一致。

    Args:
        dataset_id: 数据集 ID。
        max_sample: 触发下采样的阈值与目标规模，默认 50 000。
        seed: 下采样随机种子，固定后保证可复现。

    Returns:
        tuple: ``(coords, cell_ids, sampled, total_cells)``。文件缺失时前三项均为
        ``None / None / False``，``total_cells`` 为 ``0``，调用方据此判断 ``has_umap``。
    """
    processed_dir = Path(settings.PROCESSED_DIR) / str(dataset_id)
    umap_file = processed_dir / "umap_2d.npy"
    cell_ids_file = processed_dir / "cell_ids.json"
    if not umap_file.is_file() or not cell_ids_file.is_file():
        return None, None, False, 0

    try:
        coords = np.load(umap_file)
        with cell_ids_file.open(encoding="utf-8") as f:
            cell_ids: list[str] = [str(cid) for cid in json.load(f)]
    except (OSError, ValueError) as exc:
        logger.warning("读取 UMAP 文件失败 dataset_id=%s err=%s", dataset_id, exc)
        return None, None, False, 0

    if coords.ndim != 2 or coords.shape[1] < 2:
        logger.warning(
            "UMAP 坐标维度异常 dataset_id=%s shape=%s",
            dataset_id,
            coords.shape,
        )
        return None, None, False, 0

    coords = coords[:, :2].astype(np.float32, copy=False)
    total = int(coords.shape[0])
    if total != len(cell_ids):
        logger.warning(
            "UMAP 与 cell_ids 长度不一致 dataset_id=%s coords=%d cell_ids=%d",
            dataset_id,
            total,
            len(cell_ids),
        )
        n = min(total, len(cell_ids))
        coords = coords[:n]
        cell_ids = cell_ids[:n]
        total = n

    sampled = False
    if total > max_sample:
        rng = np.random.RandomState(seed)
        idx = np.sort(rng.choice(total, max_sample, replace=False))
        coords = coords[idx]
        cell_ids = [cell_ids[int(i)] for i in idx]
        sampled = True

    return coords.tolist(), cell_ids, sampled, total
