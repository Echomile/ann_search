"""ARQ Worker 配置入口。

通过 ``arq app.tasks.worker.WorkerSettings`` 启动。
"""

from __future__ import annotations

from arq.connections import RedisSettings

from app.core.config import settings
from app.tasks.benchmark_task import benchmark_index_task
from app.tasks.index_task import build_index
from app.tasks.preprocess_task import preprocess_dataset


class WorkerSettings:
    """ARQ Worker 设置类。

    Attributes:
        functions: 注册的任务函数列表。
        redis_settings: Redis 连接配置，从全局配置派生。
        max_jobs: 单 worker 最大并发任务数。
    """

    functions = [preprocess_dataset, build_index, benchmark_index_task]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    max_jobs = 4
    job_timeout = 60 * 60
    keep_result = 60 * 60
