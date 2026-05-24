"""v1 路由聚合。

注意：``aligned_datasets`` 必须早于 ``datasets`` 注册，否则 FastAPI
会先匹配 ``/datasets/{dataset_id}``（int 参数）把 ``/datasets/aligned``
当作 dataset_id 解析，回 422。
"""

from fastapi import APIRouter

from app.api.v1 import (
    aligned_datasets,
    auth,
    datasets,
    evaluation,
    indexes,
    rag,
    search,
    stats,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(auth.admin_router)
api_router.include_router(aligned_datasets.router)
api_router.include_router(datasets.router)
api_router.include_router(indexes.router)
api_router.include_router(search.router)
api_router.include_router(stats.router)
api_router.include_router(evaluation.router)
api_router.include_router(rag.router)
