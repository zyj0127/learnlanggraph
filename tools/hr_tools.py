# -*- coding: utf-8 -*-
"""HR 实体查询工具：员工档案查询 / 假期余额查询 / 证明开具 / 请假申请（LangChain @tool）。

数据访问统一走 database/repository.py（SQLite / PostgreSQL 双后端，
由 Settings.use_sqlite_fallback 切换），工具签名与返回文案保持不变。
三个查询工具同时被 mcp_server/hr_tools_server.py 以「逻辑零复制」方式复用
对外暴露为 MCP 工具。

请假申请（apply_leave）为写操作 + 敏感工具：图拓扑（human_review interrupt
先于 ToolNode）决定本工具只在审批通过后执行，故工具体即「履约」——
余额复核 + pending→approved + 年假余额扣减；预检与 pending 落库在
human_review_node（见 tools/leave_service.py 模块注释）。
"""
from langchain_core.tools import tool

from auth.guard import (
    ACTION_APPLY_LEAVE,
    ACTION_ISSUE_CERT,
    ACTION_VIEW_LEAVE,
    ACTION_VIEW_PROFILE,
    audit_cert_issued,
    audit_leave_request,
    check_tool_permission,
)
from database.repository import run_query
from tools import leave_service


@tool
def get_employee_profile(uid: str) -> str:
    """
    根据员工uid查询员工的完整人事档案，包括姓名，职级，工作城市，入职年限，基本薪资。
    当需要获取当前对话的员工的属性时，必须调用此工具
    """
    # RBAC：越权时返回固定拒答文案（AUTH_ENABLED=false 时旁路，行为不变）
    denial = check_tool_permission(ACTION_VIEW_PROFILE, uid)
    if denial is not None:
        return denial

    res = run_query(
        sql_pg="select uid,name,level,city,tenure,salary from employees where uid=:0",
        sql_sqlite="select uid,name,level,city,tenure,salary from employees where uid=?",
        params=(uid,),
    )

    if not res:
        return f"未找到uid为{uid}的员工信息"

    employee = res[0]
    return (f"档案查询结果：员工姓名{employee['name']}，级别{employee['level']}，"
            f"工作地点{employee['city']}，入职年限{employee['tenure']}年，"
            f"基本薪资{employee['salary']}元。"
            )


@tool
def get_leave_balance(uid: str) -> str:
    """
    根据员工uid 查询剩余假期余额（年假和病假）
    当员工明确提问"我还有几天假"或我的余额时调用
    """
    # RBAC：越权时返回固定拒答文案（AUTH_ENABLED=false 时旁路，行为不变）
    denial = check_tool_permission(ACTION_VIEW_LEAVE, uid)
    if denial is not None:
        return denial

    res = run_query(
        sql_pg="""SELECT a.name, b.annual_leave_remaining, b.sick_leave_remaining
            from employees a LEFT JOIN leave_balances b on a.uid = b.uid
            where a.uid = :0""",
        sql_sqlite="""SELECT a.name, b.annual_leave_remaining, b.sick_leave_remaining
            from employees a LEFT JOIN leave_balances b on a.uid = b.uid
            where a.uid = ?""",
        params=(uid,),
    )
    if not res:
        return f"无法获得uid为{uid}的假期"
    data = res[0]
    return (f"「假期系统」员工 {data['name']}(UID:{uid}) 当前剩余法定/福利年假: {data['annual_leave_remaining']} 天, "
            f"剩余带薪病假: {data['sick_leave_remaining']} 天。")


@tool
def generate_employment_certification(uid: str, cer_type: str) -> str:
    """为员工生成指定类型的证明文件。
    参数cer_type必须是以下两个值之一
    -'employment'：仅开具在职证明
    -'income'：开具包含薪资的在职收入证明（有职级权限限制）
    """
    # RBAC：越权时返回固定拒答文案（AUTH_ENABLED=false 时旁路，行为不变）
    denial = check_tool_permission(ACTION_ISSUE_CERT, uid)
    if denial is not None:
        return denial

    emp_res = run_query(
        sql_pg="select name,level,city,salary from employees where uid=:0",
        sql_sqlite="select name,level,city,salary from employees where uid=?",
        params=(uid,),
    )
    if not emp_res:
        return f"因无法核实员工身份（uid：{uid}）证明失效"

    employee = emp_res[0]
    if cer_type == "income":
        try:
            rank_level = int(employee["level"].replace("P", ""))
        except ValueError:
            rank_level = 0

        if rank_level < 5:
            return (f"系统提示：根据公司规定，P4及以下职级员工（{employee['level']}）无法开具线上薪资收入证明"
                    f"请引导员工在线提交人工工单，由HR线下手动实施开具"
                    )

        content = f"《薪资收入证明》\n兹证明我公司员工 {employee['name']}，职级为 {employee['level']}。\n该员工基本薪资为人民币 {employee['salary']} 元。\n特此证明（公章）"

        # 审计留痕：证明开具成功（只记 uid/角色/证明类型，不落薪资等 PII 值）
        from auth.context import get_current_identity

        audit_cert_issued(get_current_identity(), uid, cer_type)
        return (f"[系统成功]已为你自动生成收入证明：\n=============="
                f"{content}"
                f"\n===================="
                )
    elif cer_type == "employment":
        content = (
            f"《在职证明》\n证明 {employee['name']} 现为我公司在职员工，职级为 {employee['level']}，"
            f"基本薪资为人民币 {employee['salary']} 元。特此证明。\n（公章）")

        # 审计留痕：证明开具成功（只记 uid/角色/证明类型，不落薪资等 PII 值）
        from auth.context import get_current_identity

        audit_cert_issued(get_current_identity(), uid, cer_type)
        return (f"「系统成功」已自动为您生成在职证明：\n---\n"
                f"{content}\n---")
    return "错误：不支持的证明类型。可选类型为'employment'或'income'"


@tool
def apply_leave(uid: str, leave_type: str, start_date: str, end_date: str,
                reason: str = "") -> str:
    """为员工提交请假申请（写操作，需人工审批后生效）。

    参数：
    - uid：请假员工的 uid
    - leave_type：请假类型，必须是「年假」「病假」「事假」之一
    - start_date / end_date：起止日期，格式 YYYY-MM-DD（如 2026-03-05）
    - reason：请假事由（可选）

    本工具是敏感工具（agent/constants.py SENSITIVE_TOOLS）：图拓扑保证它只在
    人工审批通过后执行，因此函数体即履约逻辑——年假余额复核、扣减余额、
    申请单置 approved。参数预检与余额预检在审批挂起前已完成
    （agent/nodes.py human_review_node），此处复核兜底。
    """
    # RBAC：员工仅可为本人申请，HR/ADMIN 可代申请（审批后执行时身份为审批人）
    denial = check_tool_permission(ACTION_APPLY_LEAVE, uid)
    if denial is not None:
        return denial

    # 参数与余额复核（防御性：审批期间余额可能变化）
    days, error = leave_service.validate_request(leave_type, start_date, end_date)
    if error is not None:
        return error

    from auth.context import get_current_identity

    identity = get_current_identity()
    approver_uid = identity.uid or ""
    ok, text, _request_id = leave_service.fulfill_approved(
        uid, leave_type, start_date, end_date, days, reason, approver_uid,
    )
    # 审计留痕（detail 只记类型/天数等枚举值，不落事由自由文本）
    audit_leave_request(
        identity, uid,
        result="approved" if ok else "rejected",
        detail=f"{leave_type}/{days}天",
    )
    return text
