# -*- coding: utf-8 -*-
"""请假申请业务逻辑层：预检 / pending 落库 / 审批履约（双后端，零重依赖）。

为什么抽这一层
--------------
审批拓扑（chatbot → human_review interrupt → ToolNode）决定敏感工具
**只在审批通过后才执行**，因此 apply_leave 的三个阶段分散在两处：

- human_review_node（agent/nodes.py）：参数预检 + 年假余额预检
  （不足直接回 ToolMessage 提示，**不进入审批**）+ pending 落库 + reject 置 rejected
- apply_leave 工具（tools/hr_tools.py）：审批通过后履约——余额复核 +
  pending → approved + 年假扣减余额（与开证明「通过后实际开具」同一位置）

本模块把两处的数据库读写收敛为唯一实现，测试可直接按
「预检 → 落 pending → 履约 / 拒绝」序列驱动，无需拉起整张图。
"""
from datetime import datetime
from typing import Optional, Tuple

from database.repository import run_execute, run_insert_id, run_query
from logging_config import get_logger

logger = get_logger(__name__)

LEAVE_TYPES = ("年假", "病假", "事假")
DATE_FMT = "%Y-%m-%d"


def validate_request(leave_type: str, start_date: str, end_date: str
                     ) -> Tuple[Optional[int], Optional[str]]:
    """校验请假参数并计算天数。返回 (days, None) 或 (None, 提示文案)。"""
    if leave_type not in LEAVE_TYPES:
        return None, f"系统提示：不支持的请假类型「{leave_type}」，可选：{'/'.join(LEAVE_TYPES)}。"
    try:
        start = datetime.strptime(start_date.strip(), DATE_FMT)
        end = datetime.strptime(end_date.strip(), DATE_FMT)
    except (ValueError, AttributeError):
        return None, "系统提示：日期格式应为 YYYY-MM-DD（如 2026-03-05），请确认起止日期后重试。"
    days = (end - start).days + 1
    if days <= 0:
        return None, "系统提示：结束日期不能早于开始日期，请确认请假起止日期后重试。"
    return days, None


def check_annual_balance(uid: str, leave_type: str, days: int) -> Optional[str]:
    """年假余额预检：不足返回提示文案（不进入审批）；其他类型或余额充足返回 None。"""
    if leave_type != "年假":
        return None
    rows = run_query(
        sql_pg="select annual_leave_remaining from leave_balances where uid=:0",
        sql_sqlite="select annual_leave_remaining from leave_balances where uid=?",
        params=(uid,),
    )
    if not rows:
        return f"系统提示：无法核实 uid 为 {uid} 的假期余额，请联系 HR 协助核实后再提交申请。"
    remaining = rows[0]["annual_leave_remaining"]
    if remaining < days:
        return (f"系统提示：年假余额不足——当前剩余 {remaining} 天，本次申请 {days} 天。"
                "请调整请假天数，或改用其他假期类型后重新提交。")
    return None


def create_pending(uid: str, leave_type: str, start_date: str, end_date: str,
                   days: int, reason: str) -> int:
    """审批挂起时落 pending 记录，返回申请 id。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    request_id = run_insert_id(
        sql_pg="""INSERT INTO leave_requests
                  (uid, leave_type, start_date, end_date, days, reason, status, created_at)
                  VALUES (:0, :1, :2, :3, :4, :5, 'pending', :6) RETURNING id""",
        sql_sqlite="""INSERT INTO leave_requests
                  (uid, leave_type, start_date, end_date, days, reason, status, created_at)
                  VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
        params=(uid, leave_type, start_date, end_date, days, reason or "", now),
    )
    logger.info("请假申请已登记（pending）：id=%s uid=%s type=%s days=%s",
                request_id, uid, leave_type, days)
    return request_id


def _find_latest_pending(uid: str, leave_type: str, start_date: str, end_date: str
                         ) -> Optional[int]:
    """定位同一申请（uid+类型+起止）最近一条 pending 记录 id；无则 None。"""
    rows = run_query(
        sql_pg="""select id from leave_requests
                  where uid=:0 and leave_type=:1 and start_date=:2 and end_date=:3
                  and status='pending' order by id desc limit 1""",
        sql_sqlite="""select id from leave_requests
                  where uid=? and leave_type=? and start_date=? and end_date=?
                  and status='pending' order by id desc limit 1""",
        params=(uid, leave_type, start_date, end_date),
    )
    return int(rows[0]["id"]) if rows else None


def fulfill_approved(uid: str, leave_type: str, start_date: str, end_date: str,
                     days: int, reason: str, approver_uid: str,
                     request_id: Optional[int] = None
                     ) -> Tuple[bool, str, Optional[int]]:
    """审批通过后的履约（apply_leave 工具 / 管理台队列审批共用）。

    年假余额复核（审批期间余额可能变化，防御性复检）→ 扣减余额 →
    pending 置 approved。request_id 传入时按 id 定向更新（且要求当前为
    pending，防止重复履约）；否则按（uid+类型+起止）定位最近 pending，
    都找不到则兜底直插 approved 行。
    返回 (是否成功, 对外文案, 申请 id)。
    """
    denial = check_annual_balance(uid, leave_type, days)
    if denial is not None:
        # 审批通过但余额在审批期间被占用：置 rejected 并提示
        mark_rejected(uid, leave_type, start_date, end_date, approver_uid,
                      reason=reason, days=days, request_id=request_id)
        return False, denial + "（该申请已按余额不足驳回。）", None

    if leave_type == "年假":
        run_execute(
            sql_pg="""update leave_balances
                      set annual_leave_remaining = annual_leave_remaining - :0
                      where uid=:1""",
            sql_sqlite="""update leave_balances
                      set annual_leave_remaining = annual_leave_remaining - ?
                      where uid=?""",
            params=(days, uid),
        )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if request_id is None:
        request_id = _find_latest_pending(uid, leave_type, start_date, end_date)
    if request_id is not None:
        run_execute(
            sql_pg="""update leave_requests
                      set status='approved', approver=:0, decided_at=:1
                      where id=:2 and status='pending'""",
            sql_sqlite="""update leave_requests
                      set status='approved', approver=?, decided_at=?
                      where id=? and status='pending'""",
            params=(approver_uid or "", now, request_id),
        )
    else:
        # 兜底：无 pending 记录（如 AUTH_ENABLED=false 的旧拓扑行为）直插 approved 行
        request_id = run_insert_id(
            sql_pg="""INSERT INTO leave_requests
                      (uid, leave_type, start_date, end_date, days, reason, status,
                       approver, created_at, decided_at)
                      VALUES (:0, :1, :2, :3, :4, :5, 'approved', :6, :7, :7) RETURNING id""",
            sql_sqlite="""INSERT INTO leave_requests
                      (uid, leave_type, start_date, end_date, days, reason, status,
                       approver, created_at, decided_at)
                      VALUES (?, ?, ?, ?, ?, ?, 'approved', ?, ?, ?)""",
            params=(uid, leave_type, start_date, end_date, days, reason or "",
                    approver_uid or "", now, now),
        )
        logger.warning("请假履约未找到 pending 记录，兜底直插 approved：id=%s", request_id)

    logger.info("请假履约完成（approved）：id=%s uid=%s type=%s days=%s approver=%s",
                request_id, uid, leave_type, days, approver_uid or "-")
    text = (f"「系统成功」请假申请已审批通过并生效（申请编号 LR-{request_id}）：\n---\n"
            f"员工 uid：{uid}\n请假类型：{leave_type}\n"
            f"起止日期：{start_date} 至 {end_date}（共 {days} 天）\n"
            + (f"事由：{reason}\n" if reason else "")
            + f"审批人：{approver_uid or '系统'}\n---\n"
            "假期余额已同步扣减（年假），可在「我的假期」中查看明细。")
    return True, text, request_id


def mark_rejected(uid: str, leave_type: str, start_date: str, end_date: str,
                  approver_uid: str, reason: str = "", days: int = 0,
                  request_id: Optional[int] = None) -> None:
    """审批拒绝（或履约期余额复检驳回）：pending 置 rejected，余额不变。

    request_id 传入时按 id 定向更新（且要求当前为 pending），否则按
    （uid+类型+起止）定位最近 pending，都找不到则兜底直插 rejected 行。
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if request_id is None:
        request_id = _find_latest_pending(uid, leave_type, start_date, end_date)
    if request_id is not None:
        run_execute(
            sql_pg="""update leave_requests
                      set status='rejected', approver=:0, decided_at=:1
                      where id=:2 and status='pending'""",
            sql_sqlite="""update leave_requests
                      set status='rejected', approver=?, decided_at=?
                      where id=? and status='pending'""",
            params=(approver_uid or "", now, request_id),
        )
    else:
        run_insert_id(
            sql_pg="""INSERT INTO leave_requests
                      (uid, leave_type, start_date, end_date, days, reason, status,
                       approver, created_at, decided_at)
                      VALUES (:0, :1, :2, :3, :4, :5, 'rejected', :6, :7, :7) RETURNING id""",
            sql_sqlite="""INSERT INTO leave_requests
                      (uid, leave_type, start_date, end_date, days, reason, status,
                       approver, created_at, decided_at)
                      VALUES (?, ?, ?, ?, ?, ?, 'rejected', ?, ?, ?)""",
            params=(uid, leave_type, start_date, end_date, days, reason or "",
                    approver_uid or "", now, now),
        )
    logger.info("请假申请已驳回（rejected）：uid=%s type=%s approver=%s",
                uid, leave_type, approver_uid or "-")
