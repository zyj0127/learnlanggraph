# -*- coding: utf-8 -*-
"""管理台 API 单测：请假审批队列（第二条审批通道）+ 安全看板。

运行：python -m unittest test.test_admin_queue -v

设计：FastAPI TestClient + 注入测试 JWT 密钥签发 token（对齐 test_api.py 模式）；
数据走 SQLite 实测（setUp 重建确定性花名册 db）。零外部依赖环境由
conftest collect_ignore 整模块跳过（缺 fastapi/langgraph 时）。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from database.mock_db import init_db
from database.repository import run_query
from tools import leave_service

_TEST_SECRET = "admin-queue-test-secret"


def _annual_remaining(uid: str) -> int:
    rows = run_query(
        sql_pg="select annual_leave_remaining from leave_balances where uid=:0",
        sql_sqlite="select annual_leave_remaining from leave_balances where uid=?",
        params=(uid,),
    )
    return rows[0]["annual_leave_remaining"]


class AdminQueueTest(unittest.TestCase):
    def setUp(self):
        init_db()  # 重建确定性库（uid 1003 年假 14 天；历史示例均为非 pending）
        from fastapi.testclient import TestClient

        import api.server as server

        stub = SimpleNamespace(
            stream=lambda *a, **k: iter([]),
            get_state=lambda config: SimpleNamespace(next=(), values={}),
        )
        patcher = patch.object(server, "hr_agent_app", stub)
        patcher.start()
        self.addCleanup(patcher.stop)

        fake_settings = SimpleNamespace(
            auth_enabled=True, jwt_secret=_TEST_SECRET, jwt_algorithm="HS256",
        )
        patcher2 = patch.object(server, "get_settings", lambda: fake_settings)
        patcher2.start()
        self.addCleanup(patcher2.stop)

        self.client = TestClient(server.app)

    def _token(self, uid: str, role: str) -> str:
        from auth.jwt_tokens import encode_token
        from auth.models import Identity, Role

        return encode_token(Identity(uid=uid, name="", role=Role(role)),
                            secret=_TEST_SECRET)

    def _auth(self, uid: str, role: str) -> dict:
        return {"Authorization": f"Bearer {self._token(uid, role)}"}

    def _create_pending(self, uid="1003", leave_type="年假",
                        start="2026-07-01", end="2026-07-02", reason="队列测试") -> int:
        days, error = leave_service.validate_request(leave_type, start, end)
        assert error is None
        return leave_service.create_pending(uid, leave_type, start, end, days, reason)

    # ---- 权限 ----
    def test_employee_forbidden(self):
        resp = self.client.get("/api/admin/leave-requests",
                               headers=self._auth("1001", "employee"))
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_forbidden(self):
        resp = self.client.get("/api/admin/leave-requests")
        self.assertEqual(resp.status_code, 403)
        resp = self.client.get("/api/admin/security-summary")
        self.assertEqual(resp.status_code, 403)

    # ---- 队列列表 ----
    def test_list_pending_with_employee_name(self):
        rid = self._create_pending()
        resp = self.client.get("/api/admin/leave-requests?status=pending",
                               headers=self._auth("hr01", "hr"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "pending")
        self.assertEqual(data["count"], 1)
        item = data["items"][0]
        self.assertEqual(item["id"], rid)
        self.assertEqual(item["name"], "王五")  # join employees 取名（uid 1003 固定值）
        self.assertEqual(item["leave_type"], "年假")
        self.assertEqual(item["days"], 2)

    def test_list_status_filter_and_invalid(self):
        self._create_pending()
        resp = self.client.get("/api/admin/leave-requests?status=rejected",
                               headers=self._auth("hr01", "hr"))
        # 预置示例里有一条 rejected（uid 1002）
        self.assertTrue(all(i["status"] == "rejected" for i in resp.json()["items"]))
        resp = self.client.get("/api/admin/leave-requests?status=bogus",
                               headers=self._auth("hr01", "hr"))
        self.assertEqual(resp.status_code, 400)

    # ---- 审批决定 ----
    def test_approve_deducts_balance(self):
        rid = self._create_pending()  # 1003 年假 2 天
        before = _annual_remaining("1003")
        resp = self.client.post(f"/api/admin/leave-requests/{rid}/approve",
                                headers=self._auth("hr01", "hr"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], "approved")
        self.assertEqual(_annual_remaining("1003"), before - 2)
        # 状态同源：队列里该工单不再是 pending
        resp = self.client.get("/api/admin/leave-requests?status=pending",
                               headers=self._auth("hr01", "hr"))
        self.assertEqual(resp.json()["count"], 0)

    def test_reject_keeps_balance(self):
        rid = self._create_pending()
        before = _annual_remaining("1003")
        resp = self.client.post(f"/api/admin/leave-requests/{rid}/reject",
                                headers=self._auth("hr01", "hr"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "rejected")
        self.assertEqual(_annual_remaining("1003"), before)

    def test_double_decide_conflict(self):
        rid = self._create_pending()
        self.client.post(f"/api/admin/leave-requests/{rid}/approve",
                         headers=self._auth("hr01", "hr"))
        resp = self.client.post(f"/api/admin/leave-requests/{rid}/reject",
                                headers=self._auth("hr01", "hr"))
        self.assertEqual(resp.status_code, 409)

    def test_not_found(self):
        resp = self.client.post("/api/admin/leave-requests/99999/approve",
                                headers=self._auth("hr01", "hr"))
        self.assertEqual(resp.status_code, 404)

    def test_self_approval_denied(self):
        """队列场景审批人 ≠ 申请人：HR 身份但 uid 与工单相同 → 403。"""
        rid = self._create_pending(uid="1003")
        resp = self.client.post(f"/api/admin/leave-requests/{rid}/approve",
                                headers=self._auth("1003", "hr"))
        self.assertEqual(resp.status_code, 403)
        self.assertIn("同一人", resp.json()["detail"])

    # ---- 安全看板 ----
    def test_security_summary_structure(self):
        resp = self.client.get("/api/admin/security-summary?days=7",
                               headers=self._auth("admin01", "admin"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for key in ("total", "by_action_result", "by_role", "approvals",
                    "access_denied_total", "access_denied_top_actions",
                    "cert_issued", "period_days", "action_labels", "role_labels"):
            self.assertIn(key, data)
        self.assertEqual(data["period_days"], 7)
        self.assertEqual(set(data["approvals"]), {"approved", "rejected", "denied"})


if __name__ == "__main__":
    unittest.main()
