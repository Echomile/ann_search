"""ARQ Worker 配置入口。

通过 ``arq app.tasks.worker.WorkerSettings`` 启动。
"""

from __future__ import annotations

from arq.connections import RedisSettings

from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.tasks.benchmark_task import benchmark_index_task
from app.tasks.index_task import build_index
from app.tasks.preprocess_task import preprocess_dataset

logger = get_logger(__name__)


async def startup(ctx: dict) -> None:  # noqa: ARG001
    """Worker 进程启动钩子。

    在第一个真实任务到达之前，预热 numba JIT 编译密集型依赖
    （scanpy + umap-learn），避免首次 preprocess 任务卡 60 秒。
    """
    setup_logging()
    try:
        import numpy as np  # noqa: PLC0415
        import umap  # noqa: PLC0415

        logger.info("worker 预热：触发 umap-learn JIT 编译 ...")
        _ = umap.UMAP(n_neighbors=5, n_components=2, random_state=0).fit_transform(
            np.random.rand(20, 4).astype("float32")
        )
        logger.info("worker 预热完成")
    except Exception as exc:  # noqa: BLE001
        logger.warning("worker 预热失败（不影响功能）：%s", exc)


class WorkerSettings:
    """ARQ Worker 设置类。

    Attributes:
        functions: 注册的任务函数列表。
        redis_settings: Redis 连接配置，从全局配置派生。
        max_jobs: 单 worker 最大并发任务数。
        on_startup: worker 启动钩子，用于预热 JIT 等耗时初始化。
    """

    functions = [preprocess_dataset, build_index, benchmark_index_task]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_jobs = 4
    job_timeout = 60 * 60
    keep_result = 60 * 60
    on_startup = startup
