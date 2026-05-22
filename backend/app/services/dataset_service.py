"""数据集业务服务层。

把 :class:`app.models.dataset.Dataset` 相关的 DB / 磁盘逻辑从 HTTP 层剥离，
便于复用与单元测试。

约定：
    - 数据集所有权由调用方校验，本模块不负责权限判断；
    - 删除操作会尽力清理磁盘文件，但不会因 IO 异常阻断主流程；
    - 流式写入分块大小 ``CHUNK_SIZE`` 设为 8 MB，适配 GB 级 .h5ad。
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.dataset import Dataset

logger = get_logger(__name__)

CHUNK_SIZE = 8 * 1024 * 1024


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


async def stream_to_disk(reader: "BinaryIO | object", dest: Path) -> int:
    """把上传的二进制流分块写入磁盘，返回写入字节数。

    ``reader`` 需支持异步 ``read(size)``（FastAPI ``UploadFile`` 即如此），
    分块大小受 :data:`CHUNK_SIZE` 控制，避免 OOM。

    Args:
        reader: 提供 ``async read(size)`` 的对象。
        dest: 目标文件路径。

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
    return total


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
