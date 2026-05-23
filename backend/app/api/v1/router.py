"""v1 路由聚合。"""

from fastapi import APIRouter

from app.api.v1 import auth, datasets, evaluation, indexes, rag, search, stats

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(auth.admin_router)
api_router.include_router(datasets.router)
api_router.include_router(indexes.router)
api_router.include_router(search.router)
api_router.include_router(stats.router)
api_router.include_router(evaluation.router)
api_router.include_router(rag.router)
