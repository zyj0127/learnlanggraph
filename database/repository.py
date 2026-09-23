# -*- coding: utf-8 -*-
"""HR 实体查询数据访问层：统一 SQLite / PostgreSQL 双后端。

tools/hr_tools.py 与 mcp_server 只调用本模块的三个查询函数，
返回字典列表（与 database/mock_db.query_db 口径一致），
工具签名与对外文案完全不变。

后端选择由 database/session.use_postgres() 决定：
- SQLite 回退：直接复用 mock_db 的 get_connection/query_db（原逻辑原封不动）
- PostgreSQL：SQLAlchemy Session + ORM 查询
"""
from typing import Dict, List

from database.session import use_postgres
from logging_config import get_logger

logger = get_logger(__name__)


def _query_pg(sql, params: tuple) -> List[Dict]:
    """PostgreSQL 路径：SQLAlchemy 原生 SQL 查询（SQL 与 SQLite 版逐字一致）。"""
    from sqlalchemy import text  # 延迟导入

    from database.session import get_session

    session = get_session()
    try:
        rows = session.execute(text(sql), dict(enumerate(params))).mappings().all()
        return [dict(r) for r in rows]
    finally:
        session.close()


def _query_sqlite(sql: str, params: tuple) -> List[Dict]:
    """SQLite 回退路径：每次调用新建独立连接（与原 _open_db 语义一致）。"""
    from database.mock_db import get_connection, query_db

    conn = get_connection()
    try:
        return query_db(conn=conn, sql=sql, params=params)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def run_query(sql_pg: str, sql_sqlite: str, params: tuple = ()) -> List[Dict]:
    """按当前后端执行查询。

    sql_pg 使用 psycopg/SQLAlchemy 命名占位（:0, :1 ...），sql_sqlite 使用 ? 占位；
    两条 SQL 除占位符外保持逐字一致，保证双后端行为同源。
    """
    if use_postgres():
        return _query_pg(sql_pg, params)
    return _query_sqlite(sql_sqlite, params)
