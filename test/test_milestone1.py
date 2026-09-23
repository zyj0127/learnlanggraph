"""里程碑1：HR 工具功能测试（查档案 / 查假期 / 开证明）。

RBAC 改造后工具执行前会从 contextvars 取当前身份校验权限（匿名=最低权限，
一律拦截）。本套件测的是工具功能本身而非权限，因此每个用例先通过
auth.context.set_current_identity 设置与目标 uid 匹配的员工身份
（员工只能操作自己的数据，语义与改造前一致），用完复位。
"""
import unittest

from auth.context import reset_current_identity, set_current_identity
from auth.models import Identity, Role
from tools.hr_tools import get_employee_profile, get_leave_balance, generate_employment_certification


def _as_employee(uid: str, name: str):
    """设置当前请求身份为指定员工，返回 Token 供复位。"""
    return set_current_identity(Identity(uid=uid, name=name, role=Role.EMPLOYEE))


def test_get_employee_profile():
    """测试1：查看 张三的档案，因包含姓名和职级"""
    token = _as_employee('1001', '张三')
    try:
        result = get_employee_profile.invoke({'uid': '1001'})
    finally:
        reset_current_identity(token)
    assert '张三' in result
    assert 'P5' in result

def test_get_leave_balance():
    """测试2：查看 李四（1002）的剩余假期"""
    token = _as_employee('1002', '李四')
    try:
        result = get_leave_balance.invoke({'uid': '1002'})
    finally:
        reset_current_identity(token)
    assert '李四' in result
    assert '7' in result

def test_get_employment_certificate_p5():
    """测试3：查看 张三（P5）的收入证明（预期成功）"""
    token = _as_employee('1001', '张三')
    try:
        result = generate_employment_certification.invoke({'uid': '1001', 'cer_type': 'income'})
    finally:
        reset_current_identity(token)
    assert '系统成功' in result
    assert '收入证明' in result

def test_get_employment_certificate_p4():
    """测试4：查看 李四（P4）的收入证明（预期失败：职级不足的业务规则）"""
    token = _as_employee('1002', '李四')
    try:
        result = generate_employment_certification.invoke({'uid': '1002', 'cer_type': 'income'})
    finally:
        reset_current_identity(token)
    assert '无法' in result




if __name__ == '__main__':
    print('查看张三档案')
    print(get_employee_profile.invoke({'uid':'1001'}))

    print('查看李四余额假期')
    print(get_leave_balance.invoke({'uid': '1002'}))
    print('查看张三（p5收入证明）')
    print(generate_employment_certification.invoke({'uid': '1001', 'cer_type': 'employment'}))
    print('查看李四（p4收入证明）')
    print(generate_employment_certification.invoke({'uid': '1002', 'cer_type': ''}))
