# -*- coding: utf-8 -*-
"""认证授权包（auth）：身份模型 + RBAC 权限规则 + 请求身份上下文。

模块拆分：
- auth/models.py      Identity 数据模型与角色枚举（EMPLOYEE / HR / ADMIN / ANONYMOUS）
- auth/permissions.py 权限规则纯函数（可单测，对齐 agent/fact_rules.py 风格）
- auth/context.py     基于 contextvars 的「当前请求身份」上下文
- auth/guard.py       工具层强制校验入口（拒答文案 + 审计计数 + 埋点）
- auth/jwt_tokens.py  JWT 签发/校验（PyJWT 延迟导入；预留 RS256/OIDC JWKS 扩展点）

import 轻量约定：models / permissions / context 不依赖任何第三方包，
config、observability、telemetry、PyJWT 全部在使用点延迟导入，
保证纯逻辑测试在零外部依赖环境下可运行。
"""
from auth.context import get_current_identity, reset_current_identity, set_current_identity
from auth.models import ANONYMOUS_IDENTITY, Identity, Role
from auth.permissions import (
    can_approve,
    can_issue_certification,
    can_view_leave_balance,
    can_view_profile,
)

__all__ = [
    "ANONYMOUS_IDENTITY",
    "Identity",
    "Role",
    "can_approve",
    "can_issue_certification",
    "can_view_leave_balance",
    "can_view_profile",
    "get_current_identity",
    "reset_current_identity",
    "set_current_identity",
]
