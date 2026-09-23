# -*- coding: utf-8 -*-
"""数据库会话工厂：PostgreSQL（SQLAlchemy）主路径 + SQLite 开发回退。

开关口径（config.Settings）：
- use_sqlite_fallback = True（默认）  → 实体库走既有 SQLite（db/employees.db），
  无 pg 环境下工具、测试、评测全部按原路径运行，行为零变化。
- use_sqlite_fallback = False         → 走 DATABASE_URL 指向的 PostgreSQL，
  由 Alembic 迁移建表 + database/seed.py 灌入 80 人花名册。

import 轻量约定：sqlalchemy 仅在 pg 路径首次使用时延迟导入。
"""
from functools import lru_cache

from config import get_settings
from logging_config import get_logger

logger = get_logger(__name__)


def use_postgres() -> bool:
    """当前进程是否走 PostgreSQL 实体库路径。"""
    return not get_settings().use_sqlite_fallback


def normalize_database_url(url: str) -> str:
    """把 postgres:// / postgresql:// 归一化为 SQLAlchemy psycopg 驱动串。"""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


@lru_cache(maxsize=1)
def get_engine():
    """构建并缓存 PostgreSQL Engine（首次调用时建立连接池）。"""
    from sqlalchemy import create_engine  # 重依赖，延迟导入

    url = normalize_database_url(get_settings().database_url)
    logger.info("初始化 PostgreSQL Engine：%s", url.split("@")[-1])
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def get_session():
    """新建一个 SQLAlchemy Session（调用方负责关闭，建议配合 contextmanager）。"""
    from sqlalchemy.orm import Session  # 重依赖，延迟导入

    return Session(get_engine())
