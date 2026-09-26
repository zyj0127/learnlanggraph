# -*- coding: utf-8 -*-
"""请假申请（apply_leave）单测：RBAC / 余额校验 / 审批链路状态机（双后端之 SQLite 实测）。

运行：python -m unittest test.test_leave_request -v

零外部依赖设计：
- SQLite 路径实测（setUp 重建确定性花名册 db，uid 1003 年假 14 天、uid 1004 年假 2 天）；
- 工具级测试经 auth.context 注入身份，不拉起整张图、不调 LLM；
- 审批链路按「预检 → create_pending → fulfill_approved / mark_rejected」
  service 序列驱动（图内同函数被 human_review_node 与 apply_leave 工具调用）；
- pg 路径标 needs_pg（CI_PG_TESTS=1 开启）。
"""
import unittest

import pytest

from auth.context import reset_current_identity, set_current_identity
from auth.guard import (
    ACTION_APPLY_LEAVE,
    DENIAL_TEXTS,
    SELF_APPROVAL_DENIAL,
    check_approval_allowed,
)
from auth.models import Identity, Role
from database.mock_db import init_db
from database.repository import run_query
from tools import leave_service

# 确定性花名册固定值（mock_db._BASE_BALANCES）：1003 年假 14 天 / 1004 年假 2 天
UID_RICH = "1003"
UID_POOR = "1004"


def _annual_remaining(uid: str) -> int:
    rows = run_query(
        sql_pg="select annual_leave_remaining from leave_balances where uid=:0",
        sql_sqlite="select annual_leave_remaining from leave_balances where uid=?",
        params=(uid,),
    )
    return rows[0]["annual_leave_remaining"]


def _latest_request(uid: str) -> dict:
    rows = run_query(
        sql_pg="select * from leave_requests where uid=:0 order by id desc limit 1",
        sql_sqlite="select * from leave_requests where uid=? order by id desc limit 1",
        params=(uid,),
    )
    return rows[0]


def _request_count(uid: str) -> int:
    rows = run_query(
        sql_pg="select count(*) as c from leave_requests where uid=:0",
        sql_sqlite="select count(*) as c from leave_requests where uid=?",
        params=(uid,),
    )
    return rows[0]["c"]


try:
    import langchain_core  # noqa: F401

    HAS_LANGCHAIN = True
except ModuleNotFoundError:
    HAS_LANGCHAIN = False


class ValidateRequestTest(unittest.TestCase):
    """参数校验纯函数：类型 / 日期格式 / 起止顺序 / 天数计算。"""

    def test_valid(self):
        days, error = leave_service.validate_request("年假", "2026-03-02", "2026-03-04")
        self.assertEqual(days, 3)
        self.assertIsNone(error)

    def test_invalid_type(self):
        days, error = leave_service.validate_request("调休", "2026-03-02", "2026-03-02")
        self.assertIsNone(days)
        self.assertIn("不支持的请假类型", error)

    def test_invalid_date_format(self):
        days, error = leave_service.validate_request("年假", "2026/03/02", "2026-03-04")
        self.assertIsNone(days)
        self.assertIn("YYYY-MM-DD", error)

    def test_end_before_start(self):
        days, error = leave_service.validate_request("病假", "2026-03-05", "2026-03-04")
        self.assertIsNone(days)
        self.assertIn("结束日期不能早于开始日期", error)


@unittest.skipUnless(HAS_LANGCHAIN, "缺少 langchain_core，跳过工具级测试（对齐 test_auth_rbac 模式）")
class LeaveToolTest(unittest.TestCase):
    """工具级：RBAC + 余额校验 + 履约（SQLite 实测，每个用例重建确定性库）。"""

    def setUp(self):
        init_db()  # 重建 db/employees.db：80 人花名册 + 2 条历史请假记录
        self.addCleanup(self._reset_identity)
        self._token = None

    def _reset_identity(self):
        if self._token is not None:
            reset_current_identity(self._token)

    def _as(self, uid: str, role: Role):
        self._token = set_current_identity(Identity(uid=uid, name="", role=role))

    def _apply(self, uid, leave_type="年假", start="2026-03-02", end="2026-03-03",
               reason="测试事由"):
        from tools.hr_tools import apply_leave

        return apply_leave.invoke({
            "uid": uid, "leave_type": leave_type,
            "start_date": start, "end_date": end, "reason": reason,
        })

    def test_employee_self_apply_success(self):
        """员工给自己申请年假：履约成功，状态 approved，余额 14 → 12。"""
        self._as(UID_RICH, Role.EMPLOYEE)
        before = _annual_remaining(UID_RICH)
        result = self._apply(UID_RICH, end="2026-03-03")
        self.assertIn("「系统成功」", result)
        self.assertIn("LR-", result)
        self.assertEqual(_annual_remaining(UID_RICH), before - 2)
        row = _latest_request(UID_RICH)
        self.assertEqual(row["status"], "approved")
        self.assertEqual(row["days"], 2)

    def test_employee_apply_for_other_denied(self):
        """员工给他人申请：RBAC 固定拒答文案，不落库。"""
        self._as(UID_RICH, Role.EMPLOYEE)
        before = _request_count("1002")
        result = self._apply("1002")
        self.assertEqual(result, DENIAL_TEXTS[ACTION_APPLY_LEAVE])
        self.assertEqual(_request_count("1002"), before)

    def test_anonymous_apply_denied(self):
        self._as("", Role.ANONYMOUS)
        result = self._apply(UID_RICH)
        self.assertEqual(result, DENIAL_TEXTS[ACTION_APPLY_LEAVE])

    def test_insufficient_annual_balance(self):
        """年假余额不足：返回提示文案，状态不落 approved，余额不变。"""
        self._as(UID_POOR, Role.EMPLOYEE)
        before = _annual_remaining(UID_POOR)  # 2 天
        result = self._apply(UID_POOR, start="2026-03-02", end="2026-03-06")  # 5 天
        self.assertIn("年假余额不足", result)
        self.assertEqual(_annual_remaining(UID_POOR), before)
        row = _latest_request(UID_POOR)
        self.assertNotEqual(row["status"], "approved")

    def test_hr_apply_on_behalf(self):
        """HR 代员工申请事假（不扣年假余额）：履约成功。"""
        self._as("hr01", Role.HR)
        before = _annual_remaining("1002")
        result = self._apply("1002", leave_type="事假", start="2026-03-10",
                             end="2026-03-10", reason="代申请")
        self.assertIn("「系统成功」", result)
        self.assertEqual(_annual_remaining("1002"), before)  # 事假不扣年假
        self.assertEqual(_latest_request("1002")["status"], "approved")


class LeaveApprovalFlowTest(unittest.TestCase):
    """审批链路状态机：pending → approved（扣余额）/ rejected（余额不变）。

    按 human_review_node 与 apply_leave 工具实际调用的同一组 service 函数驱动，
    覆盖「挂起落 pending → 审批决定 → 履约/驳回」全链路。
    """

    def setUp(self):
        init_db()

    def test_approve_flow(self):
        uid = UID_RICH
        before = _annual_remaining(uid)
        # 1. 挂起前预检（human_review_node 同款调用）
        days, error = leave_service.validate_request("年假", "2026-04-01", "2026-04-02")
        self.assertIsNone(error)
        self.assertIsNone(leave_service.check_annual_balance(uid, "年假", days))
        # 2. 落 pending
        request_id = leave_service.create_pending(uid, "年假", "2026-04-01",
                                                  "2026-04-02", days, "家中装修")
        self.assertEqual(_latest_request(uid)["status"], "pending")
        # 3. approve → 工具履约
        ok, text, rid = leave_service.fulfill_approved(
            uid, "年假", "2026-04-01", "2026-04-02", days, "家中装修", "hr02")
        self.assertTrue(ok)
        self.assertEqual(rid, request_id)
        row = _latest_request(uid)
        self.assertEqual(row["status"], "approved")
        self.assertEqual(row["approver"], "hr02")
        self.assertEqual(_annual_remaining(uid), before - days)

    def test_reject_flow(self):
        uid = UID_RICH
        before = _annual_remaining(uid)
        leave_service.create_pending(uid, "年假", "2026-05-11", "2026-05-11", 1, "私事")
        leave_service.mark_rejected(uid, "年假", "2026-05-11", "2026-05-11", "hr02")
        row = _latest_request(uid)
        self.assertEqual(row["status"], "rejected")
        self.assertEqual(row["approver"], "hr02")
        self.assertEqual(_annual_remaining(uid), before)  # 余额不变

    def test_precheck_blocks_insufficient_balance(self):
        """预检拦截（审批挂起前）：余额不足直接返回文案，不进入审批、不落 pending。"""
        uid = UID_POOR
        before = _request_count(uid)
        days, _ = leave_service.validate_request("年假", "2026-03-02", "2026-03-06")
        denial = leave_service.check_annual_balance(uid, "年假", days)
        self.assertIsNotNone(denial)
        self.assertIn("年假余额不足", denial)
        self.assertEqual(_request_count(uid), before)

    def test_self_approval_denied(self):
        """防自审自批规则对请假负载自动适用（payload 带申请人 uid）。"""
        approver = Identity(uid=UID_RICH, name="", role=Role.HR)
        self.assertEqual(check_approval_allowed(approver, UID_RICH), SELF_APPROVAL_DENIAL)
        self.assertIsNone(check_approval_allowed(
            Identity(uid="hr02", name="", role=Role.HR), UID_RICH))


@pytest.mark.needs_pg
class LeavePgTest(unittest.TestCase):
    """pg 路径冒烟（CI_PG_TESTS=1 开启）：与 SQLite 同一组 service 函数。"""

    def test_pg_pending_roundtrip(self):
        from database.session import use_postgres

        if not use_postgres():
            self.skipTest("当前非 pg 后端")
        days, error = leave_service.validate_request("事假", "2026-06-01", "2026-06-01")
        self.assertIsNone(error)
        request_id = leave_service.create_pending(
            "1001", "事假", "2026-06-01", "2026-06-01", days, "pg 冒烟")
        self.assertGreater(request_id, 0)
        leave_service.mark_rejected("1001", "事假", "2026-06-01", "2026-06-01", "hr01")


if __name__ == "__main__":
    unittest.main()
