import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / 'db' / 'employees.db'


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

    # 4. 插入员工测试数据（6个字段）
    test_employees = [
        ('1001', '张三', 'P5', '北京', 2, 18000),
        ('1002', '李四', 'P4', '成都', 4, 18000),
        ('1003', '王五', 'P7', '上海', 5, 18000),
        ('1004', '赵六', 'P3', '深圳', 0, 18000),
    ]
    cursor.executemany('INSERT INTO employees VALUES (?, ?, ?, ?, ?, ?)', test_employees)

    # 5. 插入假期余额测试数据（3个字段）
    test_balances = [
        ('1001', 6, 10),
        ('1002', 7, 12),
        ('1003', 14, 15),
        ('1004', 2, 5),
    ]
    cursor.executemany('INSERT INTO leave_balances VALUES (?, ?, ?)', test_balances)

    conn.commit()
    print('「成功」实体数据库已成功落盘')
    print(f'数据库路径: {db_path}')
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
        print('数据库连接已安全关闭。')


if __name__ == '__main__':
    print('正在执行数据库手动初始化操作')
    standalone_conn = init_db()
    close_db(standalone_conn)