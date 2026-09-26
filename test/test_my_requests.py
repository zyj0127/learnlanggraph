# -*- coding: utf-8 -*-
"""「我的工单」员工视图 API 单测：GET /api/my/leave-requests。

运行：python -m unittest test.test_my_requests -v

uid 一律从 JWT 身份取（防越权）；匿名 403；SQLite 实测（setUp 重建确定性库）。
零外部依赖环境由 conftest collect_ignore 整模块跳过（缺 fastapi/langgraph 时）。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from database.mock_db import init_db
from tools import leave_service

_TEST_SECRET = "my-requests-test-secret"


class MyLeaveRequestsTest(unittest.TestCase):
    def setUp(self):
        init_db()  # 重建确定性库：uid 1001 有 1 条 approved 历史示例，1002 有 1 条 rejected
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

    def _auth(self, uid: str, role: str) -> dict:
        from auth.jwt_tokens import encode_token
        from auth.models import Identity, Role

        token = encode_token(Identity(uid=uid, name="", role=Role(role)),
                             secret=_TEST_SECRET)
        return {"Authorization": f"Bearer {token}"}

    def test_employee_sees_only_own_requests(self):
        """员工列表只含本人工单：1001 只能看到自己的 approved 示例。"""
        leave_service.create_pending("1001", "年假", "2026-08-03", "2026-08-04", 2, "自己的事")
        leave_service.create_pending("1003", "年假", "2026-08-05", "2026-08-05", 1, "别人的事")
        resp = self.client.get("/api/my/leave-requests", headers=self._auth("1001", "employee"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        uids = {i["uid"] for i in data["items"]}
        self.assertEqual(uids, {"1001"})
        # 本人 2 条（1 条历史 approved + 1 条新 pending），倒序（新在前）
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["items"][0]["status"], "pending")
        self.assertEqual(data["items"][1]["status"], "approved")

    def test_anonymous_forbidden(self):
        resp = self.client.get("/api/my/leave-requests")
        self.assertEqual(resp.status_code, 403)

    def test_hr_sees_only_own(self):
        """HR 查「我的工单」同样只看自己（管理台批别人 ≠ 这里看别人）。"""
        leave_service.create_pending("1003", "事假", "2026-08-10", "2026-08-10", 1, "HR 自己的事")
        resp = self.client.get("/api/my/leave-requests", headers=self._auth("1003", "hr"))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["items"][0]["uid"], "1003")

    def test_empty_list_ok(self):
        """无工单的员工：200 + 空数组（不报错）。"""
        resp = self.client.get("/api/my/leave-requests", headers=self._auth("1004", "employee"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["items"], [])

    def test_status_filter(self):
        leave_service.create_pending("1001", "病假", "2026-08-20", "2026-08-20", 1, "发烧")
        resp = self.client.get("/api/my/leave-requests?status=pending",
                               headers=self._auth("1001", "employee"))
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["leave_type"], "病假")
        resp = self.client.get("/api/my/leave-requests?status=bogus",
                               headers=self._auth("1001", "employee"))
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
