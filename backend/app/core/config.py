"""全局配置模块。

通过 :class:`Settings` 从环境变量或 ``.env`` 文件中读取配置项，
并提供单例 :data:`settings` 供应用其他模块导入使用。
"""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """应用全局配置。

    Attributes:
        PROJECT_NAME: 项目名称。
        API_V1_PREFIX: v1 API 路径前缀。
        SECRET_KEY: JWT 与其他加密用途的密钥。
        ACCESS_TOKEN_EXPIRE_MINUTES: 访问令牌过期时长（分钟）。
        DATABASE_URL: 异步 PostgreSQL 连接串，形如 ``postgresql+asyncpg://...``。
        REDIS_URL: Redis 连接串，供 ARQ 与缓存使用。
        DATA_DIR: 数据根目录。
        INDEX_DIR: 索引文件目录。
        PROCESSED_DIR: 预处理后数据目录。
        LLM_PROVIDER: 大模型提供方，取值 ``mock|anthropic``，默认 ``mock``。
        LLM_MODEL: 大模型名称，例如 ``claude-opus-4-7``（Anthropic 最新 GA flagship）。
        LLM_API_KEY: 大模型 API Key（anthropic provider 在 ``ANTHROPIC_API_KEY`` 缺省时退回此值）。
        ANTHROPIC_API_KEY: Anthropic 专用 API Key，配置时优先覆盖 ``LLM_API_KEY``。
        CORS_ORIGINS: 允许的跨域来源列表。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    PROJECT_NAME: str = "ann-search"
    API_V1_PREFIX: str = "/api/v1"

    SECRET_KEY: str = Field(default="change-me-in-production")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/ann_search",
    )
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

    DATA_DIR: str = "./data"
    INDEX_DIR: str = "./data/indexes"
    PROCESSED_DIR: str = "./data/processed"

    LLM_PROVIDER: Literal["mock", "anthropic"] = "mock"
    LLM_MODEL: str = "claude-opus-4-7"
    LLM_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    CORS_ORIGINS: Annotated[list[str], NoDecode] = [
        "http://localhost:5173",
        "http://localhost:3000",
    ]

    SEARCH_CACHE_TTL_SECONDS: int = Field(
        default=300,
        description="检索结果 Redis 缓存的 TTL（秒），<= 0 时关闭缓存。",
    )

    VECTORS_DTYPE: Literal["float32", "float16"] = Field(
        default="float32",
        description=(
            "预处理向量落盘 dtype；``float16`` 节省 50% 磁盘与冷启动内存，"
            "但召回率有轻微下降，对极致召回敏感的场景保持 ``float32``。"
        ),
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_cors_origins(cls, v: object) -> object:
        """允许 ``CORS_ORIGINS`` 用逗号分隔字符串或 JSON 数组两种格式。"""
        if isinstance(v, str):
            stripped = v.strip()
            if stripped.startswith("["):
                return stripped
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局配置单例。

    使用 ``lru_cache`` 避免重复解析 ``.env`` 文件。

    Returns:
        Settings: 全局配置对象。
    """
    return Settings()


settings: Settings = get_settings()
