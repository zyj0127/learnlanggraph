# -*- coding: utf-8 -*-
"""employees 表认证扩展：password_hash + role（账号密码登录，SSO 中间态）。

Revision ID: 0003_employee_auth
Revises: 0002_leave_requests
Create Date: 2026-10-27

- password_hash：bcrypt(cost=12) 哈希，不存明文；存量行回填统一演示口令
  哈希（与 database/mock_db.DEMO_PASSWORD_HASH 同源，初始密码见 README 演示账号表）。
- role：服务端角色真源（employee/hr/admin），登录时从本表读取签进 JWT，
  客户端不再自选角色；存量行默认 employee，职能账号 8001/8002→hr、9001→admin。
- 职能账号在旧库中不存在时一并补齐（INSERT 幂等），与 mock_db 自愈逻辑对齐。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from database.mock_db import (
    DEMO_PASSWORD_HASH,
    _FUNCTIONAL_ACCOUNTS,
    _FUNCTIONAL_BALANCES,
    role_of,
)

revision: str = "0003_employee_auth"
down_revision: str | None = "0002_leave_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("employees", sa.Column("password_hash", sa.String(), nullable=True))
    op.add_column(
        "employees",
        sa.Column("role", sa.String(), nullable=False, server_default="employee"),
    )
    # 回填：存量员工统一演示口令哈希；职能账号角色按映射覆盖
    op.execute(
        sa.text("UPDATE employees SET password_hash=:h WHERE password_hash IS NULL")
        .bindparams(h=DEMO_PASSWORD_HASH)
    )
    for uid, role in (("8001", "hr"), ("8002", "hr"), ("9001", "admin")):
        op.execute(
            sa.text("UPDATE employees SET role=:r WHERE uid=:u")
            .bindparams(r=role, u=uid)
        )
    # 职能账号补齐（旧库没有这三行）：pg INSERT ... ON CONFLICT DO NOTHING
    for acc in _FUNCTIONAL_ACCOUNTS:
        op.execute(
            sa.text(
                "INSERT INTO employees (uid,name,level,city,tenure,salary,"
                "password_hash,role) VALUES (:uid,:name,:level,:city,:tenure,"
                ":salary,:ph,:role) ON CONFLICT (uid) DO NOTHING"
            ).bindparams(
                uid=acc[0], name=acc[1], level=acc[2], city=acc[3],
                tenure=acc[4], salary=acc[5],
                ph=DEMO_PASSWORD_HASH, role=role_of(acc[0]),
            )
        )
    for uid, annual, sick in _FUNCTIONAL_BALANCES:
        op.execute(
            sa.text(
                "INSERT INTO leave_balances (uid,annual_leave_remaining,"
                "sick_leave_remaining) VALUES (:uid,:a,:s) "
                "ON CONFLICT (uid) DO NOTHING"
            ).bindparams(uid=uid, a=annual, s=sick)
        )


def downgrade() -> None:
    op.drop_column("employees", "role")
    op.drop_column("employees", "password_hash")
