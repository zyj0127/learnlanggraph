# -*- coding: utf-8 -*-
"""认证授权集成行为单测：工具越权拦截 / 匿名降级 / 审批人角色校验 / 审计计数。

运行：python -m unittest test.test_auth_rbac -v

零外部依赖设计：
- guard 层的拦截/放行/旁路/计数/审批校验用 monkeypatch 的方式屏蔽 config，
  不依赖 pg、模型、API Key、FastAPI、PyJWT；
- 真实工具（LangChain @tool）级测试在缺少 langchain_core 的环境下自动跳过；
- JWT 签发/校验测试在缺少 PyJWT 的环境下自动跳过。
"""
import unittest
from unittest import mock

from auth.context import get_current_identity, reset_current_identity, set_current_identity
from auth.guard import (
    ACTION_ISSUE_CERT,
    ACTION_VIEW_LEAVE,
    ACTION_VIEW_PROFILE,
    DENIAL_TEXTS,
    check_tool_permission,
    validate_approver,
)
from auth.models import ANONYMOUS_IDENTITY, Identity, Role
from observability.audit import AUDIT_COUNTERS

try:
    import langchain_core  # noqa: F401

    HAS_LANGCHAIN = True
except ModuleNotFoundError:
    HAS_LANGCHAIN = False

try:
    import jwt  # noqa: F401

    HAS_PYJWT = True
except ModuleNotFoundError:
    HAS_PYJWT = False

EMPLOYEE = Identity(uid="1001", name="张三", role=Role.EMPLOYEE)
HR = Identity(uid="hr001", name="王人事", role=Role.HR)
ADMIN = Identity(uid="admin", name="管理员", role=Role.ADMIN)


class _GuardBase(unittest.TestCase):
    """公共脚手架：固定 auth_enabled=True，每个用例前复位身份上下文与计数器。"""

    def setUp(self):
        AUDIT_COUNTERS.reset()
        self._enabled_patcher = mock.patch("auth.guard._auth_enabled", return_value=True)
        self._enabled_patcher.start()
        self.addCleanup(self._enabled_patcher.stop)
        # 埋点写入 mock 掉：不污染真实 telemetry.db（埋点闭环由 test_auth_metrics 覆盖）
        self._sink_patcher = mock.patch("auth.guard._log_auth_event", return_value=None)
        self._sink_patcher.start()
        self.addCleanup(self._sink_patcher.stop)
        self._token = None

    def tearDown(self):
        if self._token is not None:
            reset_current_identity(self._token)
            self._token = None

    def login(self, identity):
        self._token = set_current_identity(identity)


class TestToolGuardInterception(_GuardBase):
    """guard 层：越权返回固定拒答文案（不抛异常），并记审计计数。"""

    def test_employee_query_other_profile_denied(self):
        self.login(EMPLOYEE)
        denial = check_tool_permission(ACTION_VIEW_PROFILE, "1002")
        self.assertEqual(denial, DENIAL_TEXTS[ACTION_VIEW_PROFILE])
        self.assertEqual(AUDIT_COUNTERS.snapshot()["access_denied"], 1)

    def test_employee_query_other_leave_denied(self):
        self.login(EMPLOYEE)
        denial = check_tool_permission(ACTION_VIEW_LEAVE, "1002")
        self.assertEqual(denial, DENIAL_TEXTS[ACTION_VIEW_LEAVE])

    def test_employee_issue_cert_for_other_denied(self):
        self.login(EMPLOYEE)
        denial = check_tool_permission(ACTION_ISSUE_CERT, "1002")
        self.assertEqual(denial, DENIAL_TEXTS[ACTION_ISSUE_CERT])

    def test_denial_text_contains_no_pii(self):
        """拒答文案不得携带目标员工的任何具体信息（uid 也不回显）。"""
        self.login(EMPLOYEE)
        denial = check_tool_permission(ACTION_VIEW_PROFILE, "1002")
        self.assertNotIn("1002", denial)
        self.assertTrue(denial.startswith("权限提示："))

    def test_employee_self_allowed(self):
        self.login(EMPLOYEE)
        self.assertIsNone(check_tool_permission(ACTION_VIEW_PROFILE, "1001"))
        self.assertIsNone(check_tool_permission(ACTION_VIEW_LEAVE, "1001"))
        self.assertIsNone(check_tool_permission(ACTION_ISSUE_CERT, "1001"))
        self.assertEqual(AUDIT_COUNTERS.snapshot()["access_denied"], 0)

    def test_hr_any_target_allowed(self):
        self.login(HR)
        self.assertIsNone(check_tool_permission(ACTION_VIEW_PROFILE, "1002"))
        self.assertIsNone(check_tool_permission(ACTION_ISSUE_CERT, "1002"))

    def test_admin_any_target_allowed(self):
        self.login(ADMIN)
        self.assertIsNone(check_tool_permission(ACTION_VIEW_PROFILE, "1002"))

    def test_denied_events_accumulate_in_counters(self):
        self.login(EMPLOYEE)
        check_tool_permission(ACTION_VIEW_PROFILE, "1002")
        check_tool_permission(ACTION_VIEW_LEAVE, "1003")
        snapshot = AUDIT_COUNTERS.snapshot()
        self.assertEqual(snapshot["access_denied"], 2)
        rbac_reasons = [r for r in snapshot["block_reasons"] if r["layer"] == "rbac"]
        self.assertEqual(len(rbac_reasons), 2)


class TestAnonymousDegradation(_GuardBase):
    """匿名降级：未设置身份 / 显式匿名身份，个人数据动作全部拦截。"""

    def test_unset_context_is_anonymous(self):
        # 未调用 set_current_identity：回落匿名身份
        self.assertEqual(get_current_identity(), ANONYMOUS_IDENTITY)
        denial = check_tool_permission(ACTION_VIEW_PROFILE, "1001")
        self.assertEqual(denial, DENIAL_TEXTS[ACTION_VIEW_PROFILE])

    def test_explicit_anonymous_denied_everywhere(self):
        self.login(ANONYMOUS_IDENTITY)
        for action in (ACTION_VIEW_PROFILE, ACTION_VIEW_LEAVE, ACTION_ISSUE_CERT):
            with self.subTest(action=action):
                self.assertIsNotNone(check_tool_permission(action, "1001"))

    def test_context_reset_restores_anonymous(self):
        self.login(EMPLOYEE)
        self.assertIsNone(check_tool_permission(ACTION_VIEW_PROFILE, "1001"))
        reset_current_identity(self._token)
        self._token = None
        self.assertIsNotNone(check_tool_permission(ACTION_VIEW_PROFILE, "1001"))


class TestAuthBypass(unittest.TestCase):
    """AUTH_ENABLED=false：完全旁路，回到旧行为（匿名也能查任何人）。"""

    def test_bypass_allows_everything(self):
        AUDIT_COUNTERS.reset()
        with mock.patch("auth.guard._auth_enabled", return_value=False):
            for action in (ACTION_VIEW_PROFILE, ACTION_VIEW_LEAVE, ACTION_ISSUE_CERT):
                with self.subTest(action=action):
                    self.assertIsNone(check_tool_permission(action, "1002"))
        # 旁路不产生越权计数
        self.assertEqual(AUDIT_COUNTERS.snapshot()["access_denied"], 0)


class TestApproverValidation(unittest.TestCase):
    """审批人角色校验（审批恢复路径使用）。"""

    def test_hr_admin_pass(self):
        self.assertTrue(validate_approver(HR))
        self.assertTrue(validate_approver(ADMIN))

    def test_employee_self_approval_blocked(self):
        self.assertFalse(validate_approver(EMPLOYEE))
        self.assertFalse(validate_approver(ANONYMOUS_IDENTITY))


class TestSelfApprovalBlock(unittest.TestCase):
    """审批人 ≠ 申请人校验 + 旧负载兜底（零外部依赖）。"""

    def test_hr_self_approval_blocked(self):
        """HR 审批自己发起的证明：拒绝（口径与越权一致，返回固定文案）。"""
        from auth.guard import SELF_APPROVAL_DENIAL, check_approval_allowed

        denial = check_approval_allowed(HR, applicant_uid="hr001")
        self.assertEqual(denial, SELF_APPROVAL_DENIAL)

    def test_admin_self_approval_blocked(self):
        """ADMIN 也不能自审自批。"""
        from auth.guard import SELF_APPROVAL_DENIAL, check_approval_allowed

        self.assertEqual(check_approval_allowed(ADMIN, applicant_uid="admin"),
                         SELF_APPROVAL_DENIAL)

    def test_hr_approve_other_allowed(self):
        """HR 审批他人申请：放行。"""
        from auth.guard import check_approval_allowed

        self.assertIsNone(check_approval_allowed(HR, applicant_uid="1001"))
        self.assertIsNone(check_approval_allowed(ADMIN, applicant_uid="1001"))

    def test_employee_approve_other_blocked(self):
        """员工审批他人也不行（角色不足优先拦截）。"""
        from auth.guard import APPROVER_ROLE_DENIAL, check_approval_allowed

        self.assertEqual(check_approval_allowed(EMPLOYEE, applicant_uid="1002"),
                         APPROVER_ROLE_DENIAL)

    def test_legacy_payload_without_applicant(self):
        """旧 interrupt 负载无申请人 uid：HR 按安全默认拒绝，ADMIN 放行（break-glass）。"""
        from auth.guard import LEGACY_APPROVAL_DENIAL, check_approval_allowed

        self.assertEqual(check_approval_allowed(HR, applicant_uid=""),
                         LEGACY_APPROVAL_DENIAL)
        self.assertIsNone(check_approval_allowed(ADMIN, applicant_uid=""))

    def test_interrupt_payload_roundtrip(self):
        """负载构造与申请人提取：新负载（dict）与旧负载（纯字符串）两条路径。"""
        from auth.guard import build_interrupt_payload, extract_applicant_uid

        payload = build_interrupt_payload("msg", "1001")
        self.assertEqual(payload["message"], "msg")
        self.assertEqual(extract_applicant_uid(payload), "1001")
        # 旧负载（纯字符串）：回退到会话状态 current_uid
        self.assertEqual(extract_applicant_uid("msg", {"current_uid": "1002"}), "1002")
        self.assertEqual(extract_applicant_uid("msg"), "")
        # 负载为空串申请人时也回退会话状态
        self.assertEqual(
            extract_applicant_uid(build_interrupt_payload("msg", ""), {"current_uid": "1003"}),
            "1003",
        )


@unittest.skipUnless(HAS_LANGCHAIN, "缺少 langchain_core，跳过真实工具级测试")
class TestRealToolsRbac(unittest.TestCase):
    """真实工具（LangChain @tool）级越权拦截：SQLite 后端，不依赖 pg/模型/API Key。"""

    def setUp(self):
        AUDIT_COUNTERS.reset()
        self._enabled_patcher = mock.patch("auth.guard._auth_enabled", return_value=True)
        self._enabled_patcher.start()
        self.addCleanup(self._enabled_patcher.stop)
        sink_patcher = mock.patch("auth.guard._log_auth_event", return_value=None)
        sink_patcher.start()
        self.addCleanup(sink_patcher.stop)
        self._token = None

    def tearDown(self):
        if self._token is not None:
            reset_current_identity(self._token)

    def test_tool_returns_denial_text_when_cross_uid(self):
        from tools.hr_tools import get_employee_profile

        self._token = set_current_identity(EMPLOYEE)
        result = get_employee_profile.invoke({"uid": "1002"})
        self.assertEqual(result, DENIAL_TEXTS[ACTION_VIEW_PROFILE])

    def test_tool_works_for_self(self):
        from tools.hr_tools import get_employee_profile

        self._token = set_current_identity(EMPLOYEE)
        result = get_employee_profile.invoke({"uid": "1001"})
        # 命中档案或未找到均可，但绝不能是拒答文案（校验未误伤本人）
        self.assertNotEqual(result, DENIAL_TEXTS[ACTION_VIEW_PROFILE])


@unittest.skipUnless(HAS_PYJWT, "缺少 PyJWT，跳过 token 测试")
class TestJwtTokens(unittest.TestCase):
    def test_encode_decode_roundtrip(self):
        from auth.jwt_tokens import decode_token, encode_token

        token = encode_token(HR, secret="test-secret")
        identity = decode_token(token, secret="test-secret")
        self.assertIsNotNone(identity)
        self.assertEqual(identity.uid, HR.uid)
        self.assertEqual(identity.role, Role.HR)

    def test_wrong_secret_rejected(self):
        from auth.jwt_tokens import decode_token, encode_token

        token = encode_token(HR, secret="test-secret")
        self.assertIsNone(decode_token(token, secret="other-secret"))

    def test_garbage_token_rejected(self):
        from auth.jwt_tokens import decode_token

        self.assertIsNone(decode_token("not-a-token", secret="test-secret"))


if __name__ == "__main__":
    unittest.main()
