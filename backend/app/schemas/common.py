"""通用 schema 定义。"""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Message(BaseModel):
    """通用消息响应。"""

    detail: str = Field(..., description="提示信息")


class Pagination(BaseModel):
    """分页查询参数。"""

    page: int = Field(1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(20, ge=1, le=200, description="每页条数，最大 200")


class PageResult(BaseModel, Generic[T]):
    """通用分页响应。"""

    total: int = Field(..., description="总条数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页条数")
    items: list[T] = Field(default_factory=list, description="数据列表")
