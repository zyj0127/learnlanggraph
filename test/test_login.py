# -*- coding: utf-8 -*-
"""账号密码登录（POST /auth/login）单测：SSO 中间态，堵「客户端自选角色」口子。

运行：python -m unittest test.test_login -v

设计：FastAPI TestClient + 注入测试 JWT 密钥（对齐 test_admin_queue.py 模式）；
SQLite 实测（setUp 重建确定性花名册，含职能账号 8001=hr / 9001=admin 与统一
演示口令哈希）。零外部依赖环境由 conftest collect_ignore 整模块跳过。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from database.mock_db import DEMO_PASSWORD_HASH, init_db

_TEST_SECRET = "login-test-secret"
_DEMO_PASSWORD = "Hr@2026"  # 演示账号统一初始密码（README 演示账号表）


class LoginTest(unittest.TestCase):
    def setUp(self):
        init_db()  # 重建确定性库（含 password_hash/role 两列与职能账号）
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
            jwt_expire_minutes=120,
        )
        patcher2 = patch.object(server, "get_settings", lambda: fake_settings)
        patcher2.start()
        self.addCleanup(patcher2.stop)

        server._login_fails.clear()  # 防爆破内存计数跨用例隔离
        self.addCleanup(server._login_fails.clear)

        self.client = TestClient(server.app)

    def _login(self, uid: str, password: str, **extra):
        return self.client.post("/auth/login",
                                json={"uid": uid, "password": password, **extra})

    def _decode(self, token: str):
        from auth.jwt_tokens import decode_token

        return decode_token(token, secret=_TEST_SECRET)

    # ---- 成功路径 ----
    def test_login_success_employee(self):
        resp = self._login("1001", _DEMO_PASSWORD)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["identity"]["uid"], "1001")
        self.assertEqual(data["identity"]["name"], "张三")
        self.assertEqual(data["identity"]["role"], "employee")
        # 签出的 JWT 可被既有 decode_token 校验，契约字段不变
        identity = self._decode(data["access_token"])
        self.assertIsNotNone(identity)
        self.assertEqual(identity.uid, "1001")
        self.assertEqual(identity.role.value, "employee")

    def test_role_from_server_side(self):
        """职能账号角色取自员工表：8001 → hr，9001 → admin（非客户端声明）。"""
        resp = self._login("8001", _DEMO_PASSWORD)
        self.assertEqual(resp.json()["identity"]["role"], "hr")
        resp = self._login("9001", _DEMO_PASSWORD)
        self.assertEqual(resp.json()["identity"]["role"], "admin")

    def test_role_field_in_request_ignored(self):
        """请求体塞 role=admin 被忽略：1001 仍按员工表角色 employee 签 token。"""
        resp = self._login("1001", _DEMO_PASSWORD, role="admin")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["identity"]["role"], "employee")
        identity = self._decode(data["access_token"])
        self.assertEqual(identity.role.value, "employee")

    # ---- 失败路径：统一模糊文案 ----
    def test_wrong_password(self):
        resp = self._login("1001", "wrong-password")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"], "账号或密码错误")

    def test_unknown_user_same_message(self):
        """用户不存在与密码错误同文案同状态码（不泄露账号是否存在）。"""
        resp = self._login("9999", _DEMO_PASSWORD)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"], "账号或密码错误")

    # ---- 防爆破锁定 ----
    def test_lockout_after_five_fails(self):
        for _ in range(5):
            resp = self._login("1001", "bad")
            self.assertEqual(resp.status_code, 401)
        # 第 6 次：即使密码正确也被锁定（423）
        resp = self._login("1001", _DEMO_PASSWORD)
        self.assertEqual(resp.status_code, 423)
        self.assertIn("锁定", resp.json()["detail"])

    def test_lockout_is_per_uid(self):
        """锁定按 uid 隔离：1001 被锁不影响 1002 正常登录。"""
        for _ in range(5):
            self._login("1001", "bad")
        resp = self._login("1002", _DEMO_PASSWORD)
        self.assertEqual(resp.status_code, 200)

    # ---- 种子数据断言 ----
    def test_seed_backfills_hash_and_role(self):
        """init_db 后 employees 含 password_hash（bcrypt 串）与确定性角色。"""
        import bcrypt

        from database.repository import run_query

        rows = run_query(
            sql_pg="select uid, role, password_hash from employees",
            sql_sqlite="select uid, role, password_hash from employees",
        )
        by_uid = {r["uid"]: r for r in rows}
        self.assertEqual(len(rows), 83)  # 80 花名册 + 3 职能账号
        self.assertEqual(by_uid["8001"]["role"], "hr")
        self.assertEqual(by_uid["9001"]["role"], "admin")
        self.assertEqual(by_uid["1001"]["role"], "employee")
        for row in rows:
            self.assertEqual(row["password_hash"], DEMO_PASSWORD_HASH)
        self.assertTrue(bcrypt.checkpw(_DEMO_PASSWORD.encode(),
                                       DEMO_PASSWORD_HASH.encode()))

    def test_auth_disabled_404(self):
        """AUTH_ENABLED=false（旁路模式）时登录端点关闭（与 /auth/token 口径一致）。"""
        import api.server as server

        fake = SimpleNamespace(auth_enabled=False, jwt_secret=_TEST_SECRET,
                               jwt_algorithm="HS256", jwt_expire_minutes=120)
        patcher = patch.object(server, "get_settings", lambda: fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        resp = self._login("1001", _DEMO_PASSWORD)
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
