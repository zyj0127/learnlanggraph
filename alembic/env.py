# -*- coding: utf-8 -*-
"""Alembic env：连接串从 config.Settings(DATABASE_URL) 读取（唯一真源），
target_metadata 取自 database.models.Base（含 kb_chunks 向量表）。

支持：
- 在线迁移：alembic upgrade head
- 离线 DDL：alembic upgrade head --sql   （无 pg 实例也可验证 SQL 语法）
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from config import get_settings
from database.models import Base
from database.session import normalize_database_url

# 触发 kb_chunks（pgvector）模型注册
import database.kb_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    # 命令行 -x dburl=... 优先，其次 Settings
    x_args = context.get_x_argument(as_dictionary=True)
    return normalize_database_url(x_args.get("dburl") or get_settings().database_url)


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL 脚本，不连接数据库。"""
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连库执行迁移。"""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _get_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
