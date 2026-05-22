"""FastAPI 应用入口。

负责：
    - 创建 FastAPI 实例并配置 CORS；
    - 通过 lifespan 管理数据库与 ARQ 连接的生命周期；
    - 注册 v1 路由与全局异常处理器。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.session import async_engine

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期上下文管理器。

    Args:
        app: FastAPI 实例。

    在启动时初始化 Redis/ARQ 连接池并将其挂载到 ``app.state``；
    在关闭时释放数据库引擎与 Redis 连接。
    """
    logger.info("应用启动：初始化资源")
    arq_pool: ArqRedis | None = None
    try:
        arq_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        app.state.arq = arq_pool
    except Exception as exc:  # noqa: BLE001
        logger.warning("ARQ 连接初始化失败，将以离线模式启动：%s", exc)
        app.state.arq = None
    try:
        yield
    finally:
        logger.info("应用关闭：释放资源")
        if arq_pool is not None:
            await arq_pool.close()
        await async_engine.dispose()


app = FastAPI(
    title="单细胞 ANN 检索系统 API",
    version="0.1.0",
    description="面向单细胞高维向量数据的近似最近邻检索系统后端。",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """全局兜底异常处理器，返回统一的 500 错误响应。

    Args:
        request: 当前请求对象。
        exc: 未被显式捕获的异常。

    Returns:
        JSONResponse: 500 响应体。
    """
    logger.exception("未处理异常 path=%s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "internal server error", "type": exc.__class__.__name__},
    )


@app.get(
    "/health",
    tags=["health"],
    summary="健康检查",
    description="返回服务运行状态，可用于探活与负载均衡。",
)
async def health() -> dict[str, str]:
    """健康检查接口。

    Returns:
        dict[str, str]: 固定返回 ``{"status": "ok"}``。
    """
    return {"status": "ok"}


app.include_router(api_router, prefix=settings.API_V1_PREFIX)
