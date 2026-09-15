import random
import sqlite3
from pathlib import Path

from config import EMPLOYEES_DB
from logging_config import get_logger

logger = get_logger(__name__)

DB_PATH = EMPLOYEES_DB

# 固定种子：员工花名册可复现，评测集（eval/dataset.py）据此引用真实字段
# ⚠️ 注意：花名册规模（COMPANY_SIZE / _BASE_EMPLOYEES）变更后必须重跑 init_db()
# 重建 db/employees.db，否则磁盘库与 build_roster() 不同源，工具题评测会失配。
_ROSTER_SEED = 42

# 原有 4 名员工保持不变（test/ 下的里程碑测试依赖这些固定值）
_BASE_EMPLOYEES = [
    ('1001', '张三', 'P5', '北京', 2, 18000),
    ('1002', '李四', 'P4', '成都', 4, 18000),
    ('1003', '王五', 'P7', '上海', 5, 18000),
    ('1004', '赵六', 'P3', '深圳', 0, 18000),
]
_BASE_BALANCES = [
    ('1001', 6, 10),
    ('1002', 7, 12),
    ('1003', 14, 15),
    ('1004', 2, 5),
]

_SURNAMES = list('王李张刘陈杨黄赵周吴徐孙马朱胡郭何林罗高郑梁谢宋唐许韩冯邓曹彭')
_GIVEN = [
    '伟', '芳', '娜', '敏', '静', '磊', '军', '洋', '勇', '艳',
    '杰', '娟', '涛', '明', '超', '秀英', '霞', '平', '刚', '桂英',
    '文轩', '雨桐', '子涵', '浩然', '欣怡', '梓萱', '一诺', '思远', '嘉琪', '晨曦',
]
_CITIES = ['北京', '上海', '广州', '深圳', '香港', '成都', '杭州', '武汉', '西安', '南京']
# 职级权重：模拟一家 80 人互联网公司的纺锤形职级分布
_LEVELS = ['P3', 'P4', 'P4', 'P5', 'P5', 'P5', 'P6', 'P6', 'P6', 'P7', 'P7', 'P8', 'P9']
_SALARY_RANGE = {
    'P3': (8000, 13000), 'P4': (12000, 18000), 'P5': (18000, 26000),
    'P6': (26000, 36000), 'P7': (36000, 48000), 'P8': (50000, 70000),
    'P9': (80000, 100000),
}

COMPANY_SIZE = 80


def build_roster():
    """确定性生成 80 名员工花名册（前 4 名固定，1005-1080 由种子生成）。

    返回 (employees, balances)：employees 为 (uid,name,level,city,tenure,salary)，
    balances 为 (uid,annual_leave_remaining,sick_leave_remaining)。
    """
    rng = random.Random(_ROSTER_SEED)
    employees = list(_BASE_EMPLOYEES)
    balances = list(_BASE_BALANCES)
    used_names = {e[1] for e in employees}

    for i in range(5, COMPANY_SIZE + 1):
        uid = str(1000 + i)
        while True:
            name = rng.choice(_SURNAMES) + rng.choice(_GIVEN)
            if name not in used_names:
                used_names.add(name)
                break
        level = rng.choice(_LEVELS)
        city = rng.choice(_CITIES)
        tenure = rng.randint(0, 8)
        lo, hi = _SALARY_RANGE[level]
        salary = rng.randrange(lo, hi + 1, 1000)
        employees.append((uid, name, level, city, tenure, salary))
        balances.append((uid, rng.randint(0, 20), rng.randint(0, 15)))

    return employees, balances


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """业务运行时连接函数，仅连接并开启外键"""
    if not db_path.exists():
        raise FileNotFoundError(
            f'数据库文件未找到：{db_path}\n'
            f'请先运行初始化脚本：python database/mock_db.py'
        )
    conn = sqlite3.connect(str(db_path),check_same_thread=False)
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """
    数据库初始化（手动单次运行）
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute('PRAGMA foreign_keys=ON')
    cursor = conn.cursor()

    # 1. 创建 employees 表（主表）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS employees (
            uid TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            level TEXT,
            city TEXT,
            tenure INTEGER,
            salary INTEGER
        )
    ''')

    # 2. 创建 leave_balances 表（子表，外键依赖 employees）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leave_balances (
            uid TEXT PRIMARY KEY,
            annual_leave_remaining INTEGER NOT NULL,
            sick_leave_remaining INTEGER NOT NULL,
            FOREIGN KEY (uid) REFERENCES employees (uid)
        )
    ''')

    # 3. 清空旧数据（先删子表，再删主表，避免外键约束冲突）
    cursor.execute('DELETE FROM leave_balances')
    cursor.execute('DELETE FROM employees')

    # 4. 插入员工测试数据（80 人花名册：前 4 名固定，其余由固定种子生成）
    test_employees, test_balances = build_roster()
    cursor.executemany('INSERT INTO employees VALUES (?, ?, ?, ?, ?, ?)', test_employees)
    cursor.executemany('INSERT INTO leave_balances VALUES (?, ?, ?)', test_balances)

    conn.commit()
    logger.info('实体数据库初始化成功，已落盘：%s', db_path)
    return conn


def query_db(conn: sqlite3.Connection, sql: str, params: tuple = ()):
    """通用查询函数，返回字典列表"""
    cursor = conn.cursor()
    cursor.execute(sql, params)
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def close_db(conn: sqlite3.Connection):
    """安全关闭数据库连接"""
    if conn:
        conn.close()
        logger.info('数据库连接已安全关闭')


if __name__ == '__main__':
    logger.info('开始执行数据库手动初始化')
    standalone_conn = init_db()
    close_db(standalone_conn)