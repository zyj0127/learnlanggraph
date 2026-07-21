import sys
from pathlib import Path
from  tools.hr_tools import get_employee_profile,get_leave_balance,generate_employment_certification

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(PROJECT_ROOT)


def test_get_employee_profile():
    '''查看张三档案'''
    result = get_employee_profile.invoke({'uid': '1001'})
    assert '张三' in result
    assert 'P5' in result




if __name__ == '__main__':
    print('查看张三档案')
    print(get_employee_profile.invoke({'uid':'1001'}))

    print('查看李四余额假期')
    print(get_leave_balance.invoke({'uid': '1002'}))
    print('查看张三（p5收入证明）')
    print(generate_employment_certification.invoke({'uid': '1001', 'cer_type': 'employment'}))
    print('查看李四（p4收入证明）')
    print(generate_employment_certification.invoke({'uid': '1002', 'cer_type': ''}))