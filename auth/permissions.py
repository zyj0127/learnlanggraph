# -*- coding: utf-8 -*-
"""RBAC 权限规则纯函数（唯一真源）。

风格对齐 agent/fact_rules.py：全部为无副作用纯函数，输入 Identity +
目标 uid，输出 bool，可独立单测，不读配置、不碰数据库、不依赖 contextvars。

权限矩阵（target_uid 指被操作员工的 uid）：

| 动作 \\ 角色          | ANONYMOUS | EMPLOYEE   | HR   | ADMIN |
|----------------------|-----------|------------|------|-------|
| 查档案 view_profile   |    ×      | 仅本人     |  ✓   |  ✓    |
| 查假期 view_leave     |    ×      | 仅本人     |  ✓   |  ✓    |
| 开证明 issue_cert     |    ×      | 仅本人     |  ✓   |  ✓    |
| 请假申请 apply_leave  |    ×      | 仅本人     |  ✓   |  ✓    |
| 审批 approve          |    ×      |     ×      |  ✓   |  ✓    |

「仅本人」= identity.uid == target_uid（且 uid 非空，防空串相等误判）。
"""
from auth.models import Identity, Role

# 享有全量数据权限的角色集合
_PRIVILEGED_ROLES = (Role.HR, Role.ADMIN)


def _is_self(identity: Identity, target_uid: str) -> bool:
    """目标是否本人（双方都非空才成立，防止匿名空串与空 target 误判为本人）。"""
    return bool(identity.uid) and bool(target_uid) and identity.uid == target_uid


def can_view_profile(identity: Identity, target_uid: str) -> bool:
    """查员工档案：HR/ADMIN 任意；EMPLOYEE 仅本人；匿名拒绝。"""
    return identity.role in _PRIVILEGED_ROLES or _is_self(identity, target_uid)


def can_view_leave_balance(identity: Identity, target_uid: str) -> bool:
    """查假期余额：口径与档案一致。"""
    return identity.role in _PRIVILEGED_ROLES or _is_self(identity, target_uid)


def can_issue_certification(identity: Identity, target_uid: str) -> bool:
    """开具证明：HR/ADMIN 可为任何人发起；EMPLOYEE 仅可为本人申请；匿名拒绝。"""
    return identity.role in _PRIVILEGED_ROLES or _is_self(identity, target_uid)


def can_apply_leave(identity: Identity, target_uid: str) -> bool:
    """请假申请：HR/ADMIN 可代任何人申请；EMPLOYEE 仅可为本人申请；匿名拒绝。"""
    return identity.role in _PRIVILEGED_ROLES or _is_self(identity, target_uid)


def can_approve(identity: Identity) -> bool:
    """人工审批：仅 HR/ADMIN 可审批，防止员工自审自批。"""
    return identity.role in _PRIVILEGED_ROLES
