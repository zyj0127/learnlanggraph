# -*- coding: utf-8 -*-
"""知识库向量表（pgvector）：kb_chunks。

字段：chunk 文本、章节/段落路径、元数据 JSONB、embedding vector 列。
向量维度取自 Settings.embedding_dim（BGE-small-zh-v1.5 = 512）。

本模块依赖 sqlalchemy 与 pgvector.sqlalchemy，仅在 pgvector 路径
（Settings.vector_store == "pgvector"）或 Alembic 迁移时导入。
"""
from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from config import get_settings
from database.models import Base


class KbChunk(Base):
    """员工手册切片的向量化存储（pgvector 余弦检索）。"""

    __tablename__ = "kb_chunks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    chapter: Mapped[str | None] = mapped_column(String)   # 元数据 Chapter 路径
    section: Mapped[str | None] = mapped_column(String)   # 元数据 Section 路径
    meta: Mapped[dict | None] = mapped_column(JSONB)      # 完整原始元数据
    embedding = mapped_column(Vector(get_settings().embedding_dim), nullable=False)
