# -*- coding: utf-8 -*-
"""请假申请表：leave_requests（pending → approved / rejected）。

Revision ID: 0002_leave_requests
Revises: 0001_init
Create Date: 2026-09-25

字段与 database/mock_db.py 的 SQLite DDL 逐字对齐（uid TEXT 外键等），
保证双后端行为同源。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_leave_requests"
down_revision: str | None = "0001_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "leave_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("uid", sa.String(), sa.ForeignKey("employees.uid"), nullable=False),
        sa.Column("leave_type", sa.String(), nullable=False),  # 年假/病假/事假
        sa.Column("start_date", sa.String(), nullable=False),  # YYYY-MM-DD
        sa.Column("end_date", sa.String(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("approver", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_leave_requests_uid", "leave_requests", ["uid"])


def downgrade() -> None:
    op.drop_index("ix_leave_requests_uid", table_name="leave_requests")
    op.drop_table("leave_requests")
