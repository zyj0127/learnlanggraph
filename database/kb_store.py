# -*- coding: utf-8 -*-
"""知识库向量存储（pgvector）：kb_chunks 表的灌入与余弦检索。

幂等灌入：表为空时由调用方从 data/company_handbook.md 切块重建索引；
content 列有唯一约束，重复灌入按 ON CONFLICT DO NOTHING 跳过。

仅 pgvector 路径（Settings.vector_store == "pgvector"）触达；
内存向量库回退路径不导入本模块。
"""
from typing import List

from database.session import get_engine
from logging_config import get_logger

logger = get_logger(__name__)


def count_chunks() -> int:
    """kb_chunks 当前行数（表不存在时返回 -1，由调用方触发迁移提示）。"""
    from sqlalchemy import text  # 延迟导入

    with get_engine().connect() as conn:
        try:
            return conn.execute(text("SELECT count(*) FROM kb_chunks")).scalar()
        except Exception as e:
            logger.warning("kb_chunks 表不可用（请先执行 alembic upgrade head）：%s", e)
            return -1


def upsert_chunks(contents: List[str], metadatas: List[dict], embeddings: List[list]) -> int:
    """批量写入 chunk 向量（content 唯一冲突跳过，幂等）。返回实际插入行数。"""
    from sqlalchemy import text  # 延迟导入

    sql = text(
        "INSERT INTO kb_chunks (content, chapter, section, meta, embedding) "
        "VALUES (:content, :chapter, :section, :meta, :embedding) "
        "ON CONFLICT (content) DO NOTHING"
    )
    import json

    rows = [
        {
            "content": c,
            "chapter": (m or {}).get("Chapter"),
            "section": (m or {}).get("Section"),
            "meta": json.dumps(m or {}, ensure_ascii=False),
            "embedding": e,
        }
        for c, m, e in zip(contents, metadatas, embeddings)
    ]
    with get_engine().begin() as conn:
        result = conn.execute(sql, rows)
    inserted = result.rowcount if result.rowcount is not None else 0
    logger.info("kb_chunks 灌入完成：新增 %d / 提交 %d 条", inserted, len(rows))
    return inserted


def search_by_embedding(query_embedding: list, k: int = 5) -> List[dict]:
    """pgvector 余弦距离检索 Top-K，返回 [{content, meta}, ...]（距离升序）。"""
    from sqlalchemy import text  # 延迟导入

    sql = text(
        "SELECT content, meta, embedding <=> :q AS distance "
        "FROM kb_chunks ORDER BY embedding <=> :q LIMIT :k"
    )
    with get_engine().connect() as conn:
        rows = conn.execute(sql, {"q": str(query_embedding), "k": k}).mappings().all()
    return [{"content": r["content"], "meta": r["meta"] or {}} for r in rows]
