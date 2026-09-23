# -*- coding: utf-8 -*-
"""身份模型：Identity 与角色枚举。

只依赖标准库（dataclasses + enum），保持零第三方依赖，
纯逻辑测试与工具层校验都可直接 import。

角色语义：
- ANONYMOUS 匿名访客：未登录/无 token，最低权限（仅政策问答可用，
  涉及个人数据的工具一律被 RBAC 拦下）
- EMPLOYEE  普通员工：只能查/操作自己的档案、假期与证明
- HR        HR 专员：可查所有员工，可发起证明并担任审批人
- ADMIN     管理员：全量权限
"""
from dataclasses import dataclass, field
from enum import Enum


class Role(str, Enum):
    """系统角色（str 枚举，便于 JSON/JWT 序列化与日志输出）。"""

    ANONYMOUS = "anonymous"
    EMPLOYEE = "employee"
    HR = "hr"
    ADMIN = "admin"


@dataclass(frozen=True)
class Identity:
    """当前请求身份（不可变，随请求贯穿整个 Graph 执行）。

    - uid:        员工 uid（匿名身份为空字符串）
    - name:       姓名（仅用于展示与日志脱敏后的操作人标识）
    - role:       角色（权限判定唯一依据）
    - department: 部门（留痕/展示用，权限规则暂不引用）
    """

    uid: str = ""
    name: str = ""
    role: Role = Role.ANONYMOUS
    department: str = field(default="")

    @property
    def is_anonymous(self) -> bool:
        return self.role == Role.ANONYMOUS


# 匿名身份单例：未设置 contextvars / 无 token 时的兜底身份（最低权限）
ANONYMOUS_IDENTITY = Identity(uid="", name="匿名访客", role=Role.ANONYMOUS)
