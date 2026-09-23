# -*- coding: utf-8 -*-
"""工具层强制校验入口（guard）：越权时返回固定礼貌拒答文案，不抛异常。

设计要点：
- 规则判定委托 auth/permissions.py 纯函数，本模块只负责「编排」：
  取当前身份 → 判定 → 越权时记审计计数 + 结构化日志 + telemetry 埋点 → 返回拒答文案。
- 拒答文案风格对齐 hr_tools 既有返回格式（中文、系统提示口吻），
  且不泄露目标员工任何信息（文案不含目标姓名等 PII）。
- AUTH_ENABLED=false 时完全旁路（回到旧行为），配置读取失败按「启用」处理（安全优先）。
- config / telemetry 均延迟导入且失败静默，保证纯逻辑测试零外部依赖可跑。
"""
from typing import Callable, Optional

from auth.context import get_current_identity
from auth.models import Identity, Role
from auth.permissions import (
    can_approve,
    can_issue_certification,
    can_view_leave_balance,
    can_view_profile,
)
from logging_config import get_logger
from observability.audit import AUDIT_COUNTERS

logger = get_logger(__name__)

# 工具动作标识（埋点/审计口径的 action 字段取值）
ACTION_VIEW_PROFILE = "view_profile"
ACTION_VIEW_LEAVE = "view_leave"
ACTION_ISSUE_CERT = "issue_cert"

# 越权固定拒答文案（唯一真源；风格对齐工具既有返回文案，不含任何 PII）
DENIAL_TEXTS = {
    ACTION_VIEW_PROFILE: "权限提示：您当前的身份无权查询该员工的人事档案（仅可查询本人档案，或请联系 HR 协助）。",
    ACTION_VIEW_LEAVE: "权限提示：您当前的身份无权查询该员工的假期余额（仅可查询本人假期，或请联系 HR 协助）。",
    ACTION_ISSUE_CERT: "权限提示：您当前的身份无权为该员工开具证明（仅可为本人申请，或请联系 HR 协助）。",
}

_PERMISSION_FN: dict[str, Callable[[Identity, str], bool]] = {
    ACTION_VIEW_PROFILE: can_view_profile,
    ACTION_VIEW_LEAVE: can_view_leave_balance,
    ACTION_ISSUE_CERT: can_issue_certification,
}


def _auth_enabled() -> bool:
    """读取 AUTH_ENABLED 开关；配置不可用（如零依赖测试环境）按启用处理（安全优先）。"""
    try:
        from config import get_settings

        return bool(get_settings().auth_enabled)
    except Exception:
        return True


def is_auth_enabled() -> bool:
    """对外的开关读取入口（供 nodes/server/streamlit 使用；mock _auth_enabled 即可全覆盖）。"""
    return _auth_enabled()


# ---- 审批人校验（防止自审自批）----

# 审批拒答文案（唯一真源；HTTP 403 detail 与 Streamlit 提示复用同一口径）
APPROVER_ROLE_DENIAL = "权限提示：仅 HR 或管理员可执行人工审批。"
SELF_APPROVAL_DENIAL = "权限提示：审批人不能与申请人为同一人，请交由其他 HR 专员或管理员审批。"
# 旧 interrupt 负载无申请人 uid（升级前挂起的历史会话）：无法核实「非自批」，
# 安全默认 = HR 拒绝、ADMIN 放行（ADMIN 本就有全量数据权限，作为 break-glass 兜底）
LEGACY_APPROVAL_DENIAL = "权限提示：该审批请求缺少申请人信息（历史会话），出于安全考虑仅管理员可审批。"


def build_interrupt_payload(message: str, applicant_uid: str) -> dict:
    """构造审批挂起负载：原文案 + 申请人 uid（供恢复路径校验审批人 ≠ 申请人）。"""
    return {"message": message, "applicant_uid": applicant_uid or ""}


def extract_applicant_uid(interrupt_value, state_values: Optional[dict] = None) -> str:
    """从 interrupt 负载取申请人 uid；旧负载（纯字符串）回退到会话状态的 current_uid。"""
    if isinstance(interrupt_value, dict):
        uid = str(interrupt_value.get("applicant_uid") or "")
        if uid:
            return uid
    return str((state_values or {}).get("current_uid") or "")


def check_approval_allowed(approver: Identity, applicant_uid: str) -> Optional[str]:
    """审批恢复前的完整校验：角色 + 审批人 ≠ 申请人 + 旧负载兜底。

    返回 None 放行；否则返回固定拒答文案（不抛异常，口径与工具越权一致）。
    """
    if not can_approve(approver):
        return APPROVER_ROLE_DENIAL
    if applicant_uid:
        if approver.uid and approver.uid == applicant_uid:
            return SELF_APPROVAL_DENIAL
        return None
    # 旧负载无申请人 uid：无法核实非自批，HR 按安全默认拒绝，ADMIN 放行
    if approver.role == Role.ADMIN:
        return None
    return LEGACY_APPROVAL_DENIAL


def _log_auth_event(action: str, actor: Identity, target_uid: str, result: str, detail: str = "") -> None:
    """telemetry 埋点（失败静默，绝不影响主链路）；不落 PII 字段值。"""
    try:
        from telemetry.sink import log_auth_event

        log_auth_event(
            action=action,
            actor_uid=actor.uid or "anonymous",
            actor_role=actor.role.value,
            target_uid=target_uid,
            result=result,
            detail=detail,
        )
    except Exception:
        pass


def check_tool_permission(action: str, target_uid: str) -> Optional[str]:
    """工具执行前的 RBAC 校验。

    返回 None 表示放行；返回字符串表示越权，字符串即固定拒答文案，
    由工具直接作为返回值返回（不抛异常，不打断 Graph）。
    越权拦截会同时记一笔审计计数器与 telemetry 埋点。
    """
    if not _auth_enabled():
        return None

    identity = get_current_identity()
    permission_fn = _PERMISSION_FN[action]
    if permission_fn(identity, target_uid):
        return None

    denial = DENIAL_TEXTS[action]
    AUDIT_COUNTERS.record_access_denied(action)
    logger.warning(
        "越权拦截：actor=%s role=%s action=%s target=%s",
        identity.uid or "anonymous", identity.role.value, action, target_uid,
    )
    _log_auth_event(action, identity, target_uid, result="denied")
    return denial


def validate_approver(identity: Identity) -> bool:
    """审批人角色校验：仅 HR/ADMIN 可审批（防止员工自审自批）。"""
    return can_approve(identity)


def audit_approval(decision: str, approver: Identity, target_uid: str = "",
                   result: Optional[str] = None, detail: str = "") -> None:
    """审批留痕：结构化日志 + telemetry 埋点各一笔。

    result 默认按 decision 推导（approved/rejected）；审批被前置拦截时传 "denied"
    并以 detail 记拦截原因（self_approval / role / legacy_unverifiable）。
    """
    if result is None:
        result = "approved" if decision == "approve" else "rejected"
    logger.warning(
        "人工审批留痕：approver=%s role=%s decision=%s result=%s target=%s detail=%s",
        approver.uid or "anonymous", approver.role.value, decision, result, target_uid, detail,
    )
    _log_auth_event("approval", approver, target_uid, result=result, detail=detail)


def audit_cert_issued(identity: Identity, target_uid: str, cer_type: str) -> None:
    """证明开具成功留痕（detail 只记证明类型，不落薪资等 PII 值）。"""
    logger.info(
        "证明开具留痕：actor=%s role=%s target=%s cer_type=%s",
        identity.uid or "anonymous", identity.role.value, target_uid, cer_type,
    )
    _log_auth_event("cert_issued", identity, target_uid, result="success", detail=cer_type)
