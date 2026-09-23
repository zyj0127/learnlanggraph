# -*- coding: utf-8 -*-
"""RBAC 权限规则纯函数单测：权限矩阵全覆盖。

运行：python -m unittest test.test_permissions -v
（纯规则单测，不加载模型、不连数据库、不依赖任何第三方包，秒级完成）
"""
import unittest

from auth.models import ANONYMOUS_IDENTITY, Identity, Role
from auth.permissions import (
    can_approve,
    can_issue_certification,
    can_view_leave_balance,
    can_view_profile,
)

SELF_UID = "1001"
OTHER_UID = "1002"

EMPLOYEE = Identity(uid=SELF_UID, name="张三", role=Role.EMPLOYEE)
HR = Identity(uid="hr001", name="HR 专员", role=Role.HR)
ADMIN = Identity(uid="admin", name="管理员", role=Role.ADMIN)
ANONYMOUS = ANONYMOUS_IDENTITY

# 数据类动作的判定函数（审批无 target_uid，单独测）
DATA_ACTIONS = {
    "view_profile": can_view_profile,
    "view_leave": can_view_leave_balance,
    "issue_cert": can_issue_certification,
}


class TestDataPermissionMatrix(unittest.TestCase):
    """数据类动作（查档案 / 查假期 / 开证明）的完整角色 × 目标矩阵。"""

    def test_employee_self_allowed(self):
        for name, fn in DATA_ACTIONS.items():
            with self.subTest(action=name):
                self.assertTrue(fn(EMPLOYEE, SELF_UID))

    def test_employee_other_denied(self):
        for name, fn in DATA_ACTIONS.items():
            with self.subTest(action=name):
                self.assertFalse(fn(EMPLOYEE, OTHER_UID))

    def test_hr_any_target_allowed(self):
        for name, fn in DATA_ACTIONS.items():
            for target in (SELF_UID, OTHER_UID):
                with self.subTest(action=name, target=target):
                    self.assertTrue(fn(HR, target))

    def test_admin_any_target_allowed(self):
        for name, fn in DATA_ACTIONS.items():
            for target in (SELF_UID, OTHER_UID):
                with self.subTest(action=name, target=target):
                    self.assertTrue(fn(ADMIN, target))

    def test_anonymous_all_denied(self):
        for name, fn in DATA_ACTIONS.items():
            for target in (SELF_UID, OTHER_UID, ""):
                with self.subTest(action=name, target=target):
                    self.assertFalse(fn(ANONYMOUS, target))

    def test_empty_uid_never_counts_as_self(self):
        """空 uid 不得与空 target 误判为「本人」（防空串相等绕过 RBAC）。"""
        empty_identity = Identity(uid="", role=Role.EMPLOYEE)
        for name, fn in DATA_ACTIONS.items():
            with self.subTest(action=name):
                self.assertFalse(fn(empty_identity, ""))
                self.assertFalse(fn(EMPLOYEE, ""))


class TestApproverMatrix(unittest.TestCase):
    """审批权限：仅 HR/ADMIN，防止员工自审自批。"""

    def test_hr_and_admin_can_approve(self):
        self.assertTrue(can_approve(HR))
        self.assertTrue(can_approve(ADMIN))

    def test_employee_and_anonymous_cannot_approve(self):
        self.assertFalse(can_approve(EMPLOYEE))
        self.assertFalse(can_approve(ANONYMOUS))


class TestIdentityModel(unittest.TestCase):
    def test_anonymous_identity_singleton(self):
        self.assertTrue(ANONYMOUS_IDENTITY.is_anonymous)
        self.assertFalse(EMPLOYEE.is_anonymous)

    def test_identity_immutable(self):
        with self.assertRaises(Exception):
            EMPLOYEE.role = Role.ADMIN  # frozen dataclass


if __name__ == "__main__":
    unittest.main()
