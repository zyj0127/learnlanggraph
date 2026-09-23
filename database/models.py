# -*- coding: utf-8 -*-
"""SQLAlchemy 2.0 ORM 模型：员工 / 假期 / 证明（PostgreSQL 目标 schema）。

employees / leave_balances 字段与现有 SQLite schema 逐字对齐（uid TEXT 主键等），
保证 tools/hr_tools.py 行为不变；certifications 为企业化扩展表（开具留痕），
当前工具不回写，仅建表备用（行为不变优先）。

本模块 import 依赖 sqlalchemy —— 仅在启用了 PostgreSQL 路径或运行迁移/seed 时
才被导入（database/session.py 与 tools/hr_tools.py 均为延迟导入），
无 pg 依赖的纯逻辑测试不会触达。
"""
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有 ORM 模型的公共 Base（Alembic target_metadata 入口）。"""


class Employee(Base):
    """员工档案表（对齐 SQLite employees）。"""

    __tablename__ = "employees"

    uid: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    level: Mapped[str | None] = mapped_column(String)
    city: Mapped[str | None] = mapped_column(String)
    tenure: Mapped[int | None] = mapped_column(Integer)
    salary: Mapped[int | None] = mapped_column(Integer)


class LeaveBalance(Base):
    """假期余额表（对齐 SQLite leave_balances）。"""

    __tablename__ = "leave_balances"

    uid: Mapped[str] = mapped_column(
        String, ForeignKey("employees.uid"), primary_key=True
    )
    annual_leave_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    sick_leave_remaining: Mapped[int] = mapped_column(Integer, nullable=False)


class Certification(Base):
    """证明开具留痕表（企业化扩展，当前工具不回写，仅建表备用）。"""

    __tablename__ = "certifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(
        String, ForeignKey("employees.uid"), nullable=False, index=True
    )
    cer_type: Mapped[str] = mapped_column(String, nullable=False)  # employment / income
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict | None] = mapped_column(JSONB)  # 预留：审批人/渠道等
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
