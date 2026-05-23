"""日志配置模块。

通过 :func:`setup_logging` 在应用启动时配置统一的日志格式。
"""

import logging
import sys
from logging.config import dictConfig

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """初始化全局日志配置。

    Args:
        level: 根日志级别，默认 ``INFO``。
    """
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": LOG_FORMAT,
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "stream": sys.stdout,
                    "formatter": "default",
                },
            },
            "root": {"level": level, "handlers": ["console"]},
            "loggers": {
                "uvicorn": {"level": level, "handlers": ["console"], "propagate": False},
                "uvicorn.error": {"level": level, "handlers": ["console"], "propagate": False},
                "uvicorn.access": {"level": "INFO", "handlers": ["console"], "propagate": False},
                "sqlalchemy.engine": {"level": "WARNING", "handlers": ["console"], "propagate": False},
            },
        }
    )


def get_logger(name: str) -> logging.Logger:
    """获取指定名称的 logger。

    Args:
        name: logger 名称，通常传入 ``__name__``。

    Returns:
        logging.Logger: 配置好的 logger。
    """
    return logging.getLogger(name)
