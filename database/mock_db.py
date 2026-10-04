# -*- coding: utf-8 -*-
"""SQLite 模拟花名册：数据库初始化、确定性数据生成与通用查询。"""
import random
import sqlite3
from pathlib import Path
from typing import Dict, List, Tuple

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

# ---- 账号密码登录（SSO 中间态）：演示账号口令与角色 ----
# 统一初始密码 Hr@2026 的 bcrypt(cost=12) 哈希（一次计算固化为常量：
# 逐账号现算会让 init_db 慢约 20 秒，且演示环境共用同一口令，哈希可复用；
# 生产环境必须为每个账号单独设置口令，此处仅演示种子）。
# 明文口令只出现在 README 演示账号表，不落任何代码/数据库。
DEMO_PASSWORD_HASH = "$2b$12$Y4GnQiYtM4Kh4EmNLd8GB.O8if2ppESpWFwnSKFwJRRQHFH2LIA/6"

# 职能演示账号（uid/姓名/职级/城市/工龄/月薪）：在 80 人花名册之外追加，
# 角色分配确定性——8001/8002 为 HR、9001 为管理员，其余一律 employee。
# 追加账号不改变 build_roster() 生成的 80 人数据（eval 评测集契约不动）。
_FUNCTIONAL_ACCOUNTS = [
    ('8001', '林敏', 'P6', '北京', 6, 30000),
    ('8002', '周舟', 'P5', '上海', 3, 24000),
    ('9001', '安管理员', 'P8', '北京', 8, 60000),
]
_FUNCTIONAL_BALANCES = [('8001', 15, 15), ('8002', 15, 15), ('9001', 20, 15)]
_FUNCTIONAL_ROLES = {'8001': 'hr', '8002': 'hr', '9001': 'admin'}

# employees 表预期总行数（seed 幂等判据）：花名册 + 职能账号
EXPECTED_EMPLOYEE_ROWS = COMPANY_SIZE + len(_FUNCTIONAL_ACCOUNTS)


def role_of(uid: str) -> str:
    """账号角色（唯一服务端真源）：职能账号按映射，其余一律 employee。"""
    return _FUNCTIONAL_ROLES.get(uid, 'employee')


def build_roster() -> Tuple[List[tuple], List[tuple]]:
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


def _ensure_auth_columns(conn: sqlite3.Connection) -> None:
    """已有库的自愈升级：employees 缺 password_hash/role 列时 ALTER 补列并回填。

    - 存量员工：role 回填 'employee'（职能账号按映射），password_hash 回填统一
      演示口令哈希（DEMO_PASSWORD_HASH），不存明文。
    - 职能账号（8001/8002/9001）在旧库中不存在：INSERT OR IGNORE 补齐（含假期余额）。
    幂等：列已存在时直接返回。
    """
    cols = {row[1] for row in conn.execute('PRAGMA table_info(employees)')}
    if 'password_hash' in cols and 'role' in cols:
        return
    logger.warning('employees 表缺少认证列，执行原位升级（ALTER TABLE + 回填）')
    if 'password_hash' not in cols:
        conn.execute("ALTER TABLE employees ADD COLUMN password_hash TEXT")
    if 'role' not in cols:
        conn.execute("ALTER TABLE employees ADD COLUMN role TEXT NOT NULL DEFAULT 'employee'")
    conn.execute('UPDATE employees SET password_hash=? WHERE password_hash IS NULL',
                 (DEMO_PASSWORD_HASH,))
    for uid, role in _FUNCTIONAL_ROLES.items():
        conn.execute('UPDATE employees SET role=? WHERE uid=?', (role, uid))
    for acc in _FUNCTIONAL_ACCOUNTS:
        conn.execute(
            'INSERT OR IGNORE INTO employees VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (*acc, DEMO_PASSWORD_HASH, role_of(acc[0])),
        )
    for bal in _FUNCTIONAL_BALANCES:
        conn.execute('INSERT OR IGNORE INTO leave_balances VALUES (?, ?, ?)', bal)
    conn.commit()


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """业务运行时连接函数，仅连接并开启外键。

    全新克隆/容器首启时库文件不存在（运行时产物不入库），自动初始化并
    播种 80 人花名册（build_roster 固定种子，结果幂等），避免手工步骤。
    仅在文件缺失时触发，已有库绝不重建（init_db 会清空重插）；
    已有旧库缺认证列时原位升级（_ensure_auth_columns）。
    """
    if not db_path.exists():
        logger.warning('实体数据库不存在，自动初始化并播种：%s', db_path)
        init_db(db_path)
    conn = sqlite3.connect(str(db_path),check_same_thread=False)
    conn.execute('PRAGMA foreign_keys=ON')
    _ensure_auth_columns(conn)
    return conn


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """
    数据库初始化（手动单次运行）
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute('PRAGMA foreign_keys=ON')
    cursor = conn.cursor()

    # 1. 创建 employees 表（主表；password_hash/role 为账号密码登录扩展列）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS employees (
            uid TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            level TEXT,
            city TEXT,
            tenure INTEGER,
            salary INTEGER,
            password_hash TEXT,
            role TEXT NOT NULL DEFAULT 'employee'
        )
    ''')

    # 1.1 旧库原位升级：已有 employees 表缺认证列时先 ALTER 补齐
    # （CREATE TABLE IF NOT EXISTS 不会改已有表的 schema；回填在下方重插时完成）
    _ensure_auth_columns(conn)

    # 2. 创建 leave_balances 表（子表，外键依赖 employees）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leave_balances (
            uid TEXT PRIMARY KEY,
            annual_leave_remaining INTEGER NOT NULL,
            sick_leave_remaining INTEGER NOT NULL,
            FOREIGN KEY (uid) REFERENCES employees (uid)
        )
    ''')

    # 2.1 创建 leave_requests 表（请假申请，企业化写操作扩展）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leave_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT NOT NULL,
            leave_type TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            days INTEGER NOT NULL,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            approver TEXT,
            created_at TEXT,
            decided_at TEXT,
            FOREIGN KEY (uid) REFERENCES employees (uid)
        )
    ''')

    # 3. 清空旧数据（先删子表，再删主表，避免外键约束冲突）
    cursor.execute('DELETE FROM leave_requests')
    cursor.execute('DELETE FROM leave_balances')
    cursor.execute('DELETE FROM employees')

    # 4. 插入员工测试数据（80 人花名册：前 4 名固定，其余由固定种子生成；
    #    追加 3 个职能演示账号 8001/8002=hr、9001=admin；统一初始密码哈希）
    test_employees, test_balances = build_roster()
    rows = [
        (*e, DEMO_PASSWORD_HASH, role_of(e[0]))
        for e in (*test_employees, *_FUNCTIONAL_ACCOUNTS)
    ]
    cursor.executemany(
        'INSERT INTO employees VALUES (?, ?, ?, ?, ?, ?, ?, ?)', rows)
    cursor.executemany(
        'INSERT INTO leave_balances VALUES (?, ?, ?)',
        [*test_balances, *_FUNCTIONAL_BALANCES],
    )

    # 4.1 预置两条历史请假记录（approved / rejected 各一，演示与联调用）
    cursor.executemany(
        '''INSERT INTO leave_requests
           (uid, leave_type, start_date, end_date, days, reason, status, approver, created_at, decided_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        [
            ('1001', '年假', '2026-01-05', '2026-01-06', 2, '家中事务',
             'approved', 'hr01', '2026-01-02 10:00:00', '2026-01-02 15:30:00'),
            ('1002', '事假', '2026-01-10', '2026-01-10', 1, '个人事务',
             'rejected', 'hr01', '2026-01-08 09:00:00', '2026-01-08 11:00:00'),
        ],
    )

    conn.commit()
    logger.info('实体数据库初始化成功，已落盘：%s', db_path)
    return conn


def query_db(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> List[Dict]:
    """通用查询函数，返回字典列表"""
    cursor = conn.cursor()
    cursor.execute(sql, params)
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def close_db(conn: sqlite3.Connection) -> None:
    """安全关闭数据库连接"""
    if conn:
        conn.close()
        logger.info('数据库连接已安全关闭')


if __name__ == '__main__':
    logger.info('开始执行数据库手动初始化')
    standalone_conn = init_db()
    close_db(standalone_conn)