# -*- coding: utf-8 -*-
"""PostgreSQL 花名册 seed：把 mock_db.build_roster() 的 80 人确定性数据灌入 pg。

数据真源仍是 database/mock_db.py 的 build_roster()（固定种子 42），
与 SQLite 初始化、评测集（eval/dataset.py）严格同源。

幂等：以「employees 表行数 == COMPANY_SIZE」为已灌入判据；--force 可清空重灌。

用法：
    python -m database.seed           # 幂等灌入
    python -m database.seed --force   # 清空重灌
"""
import sys

from database.mock_db import COMPANY_SIZE, build_roster
from logging_config import get_logger

logger = get_logger(__name__)


def seed(force: bool = False) -> None:
    """把 80 人花名册写入 PostgreSQL（幂等）。"""
    from sqlalchemy import text  # 延迟导入：无 pg 环境不触达

    from database.session import get_engine

    employees, balances = build_roster()
    engine = get_engine()

    with engine.begin() as conn:
        count = conn.execute(text("SELECT count(*) FROM employees")).scalar()
        if count == COMPANY_SIZE and not force:
            logger.info("employees 已有 %d 行，与花名册规模一致，跳过 seed", count)
            return

        logger.info("开始灌入花名册（force=%s，现有 %d 行）", force, count)
        # 先删子表再删主表，避免外键约束冲突（与 mock_db.init_db 同序）
        conn.execute(text("DELETE FROM leave_balances"))
        conn.execute(text("DELETE FROM employees"))
        conn.execute(
            text("INSERT INTO employees (uid,name,level,city,tenure,salary) "
                 "VALUES (:uid,:name,:level,:city,:tenure,:salary)"),
            [dict(zip(("uid", "name", "level", "city", "tenure", "salary"), e)) for e in employees],
        )
        conn.execute(
            text("INSERT INTO leave_balances (uid,annual_leave_remaining,sick_leave_remaining) "
                 "VALUES (:uid,:annual,:sick)"),
            [dict(zip(("uid", "annual", "sick"), b)) for b in balances],
        )

    logger.info("PostgreSQL 花名册 seed 完成：%d 名员工", len(employees))


if __name__ == "__main__":
    seed(force="--force" in sys.argv)
