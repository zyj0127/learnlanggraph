# -*- coding: utf-8 -*-
"""初始迁移：启用 vector 扩展 + 建 employees / leave_balances / certifications / kb_chunks。

Revision ID: 0001_init
Revises:
Create Date: 2026-02-14

说明：
- employees / leave_balances 字段与旧 SQLite schema 逐字对齐（行为不变）。
- kb_chunks 依赖 pgvector 扩展，迁移第一步 CREATE EXTENSION IF NOT EXISTS vector。
- 向量维度取自 Settings.embedding_dim（默认 512，BGE-small-zh-v1.5）。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

from config import get_settings

revision: str = "0001_init"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMBEDDING_DIM = get_settings().embedding_dim


def upgrade() -> None:
    # pgvector 扩展（镜像 pgvector/pgvector:pg16 已内置该扩展包）
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "employees",
        sa.Column("uid", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("level", sa.String(), nullable=True),
        sa.Column("city", sa.String(), nullable=True),
        sa.Column("tenure", sa.Integer(), nullable=True),
        sa.Column("salary", sa.Integer(), nullable=True),
    )
    op.create_table(
        "leave_balances",
        sa.Column("uid", sa.String(), sa.ForeignKey("employees.uid"), primary_key=True),
        sa.Column("annual_leave_remaining", sa.Integer(), nullable=False),
        sa.Column("sick_leave_remaining", sa.Integer(), nullable=False),
    )
    op.create_table(
        "certifications",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("uid", sa.String(), sa.ForeignKey("employees.uid"), nullable=False),
        sa.Column("cer_type", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("meta", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_certifications_uid", "certifications", ["uid"])

    op.create_table(
        "kb_chunks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("content", sa.Text(), nullable=False, unique=True),
        sa.Column("chapter", sa.String(), nullable=True),
        sa.Column("section", sa.String(), nullable=True),
        sa.Column("meta", JSONB(), nullable=True),
        sa.Column("embedding", Vector(_EMBEDDING_DIM), nullable=False),
    )
    # 余弦距离检索用 ivfflat 索引（数据量小，lists=100 足够；空表上建索引合法）
    op.execute(
        "CREATE INDEX ix_kb_chunks_embedding ON kb_chunks "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    op.drop_index("ix_kb_chunks_embedding", table_name="kb_chunks")
    op.drop_table("kb_chunks")
    op.drop_index("ix_certifications_uid", table_name="certifications")
    op.drop_table("certifications")
    op.drop_table("leave_balances")
    op.drop_table("employees")
