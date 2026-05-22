"""全局配置模块。

通过 :class:`Settings` 从环境变量或 ``.env`` 文件中读取配置项，
并提供单例 :data:`settings` 供应用其他模块导入使用。
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
        LLM_API_KEY: RAG 加分项使用的大模型 API Key（可选）。
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

    LLM_API_KEY: str = ""

    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
    ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """获取全局配置单例。

    使用 ``lru_cache`` 避免重复解析 ``.env`` 文件。

    Returns:
        Settings: 全局配置对象。
    """
    return Settings()


settings: Settings = get_settings()
