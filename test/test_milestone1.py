import sys
from pathlib import Path
from  tools.hr_tools import get_employee_profile,get_leave_balance,generate_employment_certification

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(PROJECT_ROOT)


def test_get_employee_profile():
    """测试1：查看 张三的档案，因包含姓名和职级"""
    result = get_employee_profile.invoke({'uid': '1001'})
    assert '张三' in result
    assert 'P5' in result

def test_get_leave_balance():
    """测试2：查看 李四（1002）的剩余假期"""
    result = get_leave_balance.invoke({'uid': '1002'})
    assert '李四' in result
    assert '7' in result

def test_get_employment_certificate_p5():
    """测试3：查看 张三（P5）的收入证明（预期成功）"""
    result = generate_employment_certification.invoke({'uid': '1001', 'cer_type': 'income'})
    assert '系统成功' in result
    assert '收入证明' in result

def test_get_employment_certificate_p4():
    """测试4：查看 李四（P4）的收入证明（预期失败）"""
    result = generate_employment_certification.invoke({'uid': '1002', 'cer_type': 'income'})
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