# -*- coding: utf-8 -*-
"""统一日志配置：控制台 + 按大小滚动落盘，级别可由环境变量 LOG_LEVEL 控制。

用法：
    from logging_config import get_logger
    logger = get_logger(__name__)
    logger.debug(...) / logger.info(...) / logger.warning(...) / logger.error(...)

说明：
- 使用独立命名空间 `hr_agent.*`，不污染 root logger，避免 langchain/chromadb 等
  第三方库的噪音混入。
- 日志同时输出到控制台与 `logs/hr_agent.log`（5MB 滚动，保留 3 份）。
- 级别通过环境变量 `LOG_LEVEL` 设置（DEBUG/INFO/WARNING/ERROR，默认 INFO）。
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"
_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def _configure() -> None:
    global _configured
    if _configured:
        return

    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    base = logging.getLogger("hr_agent")
    base.setLevel(level)
    base.propagate = False  # 不冒泡到 root，避免重复输出

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    base.addHandler(console)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_DIR / "hr_agent.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        base.addHandler(file_handler)
    except Exception as e:  # 文件日志失败不影响控制台输出
        base.warning("文件日志初始化失败，仅使用控制台输出：%s", e)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """返回 `hr_agent.<name>` 命名空间下的 logger，首次调用时完成配置。"""
    _configure()
    return logging.getLogger("hr_agent." + name)
