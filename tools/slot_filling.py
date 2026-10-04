# -*- coding: utf-8 -*-
"""工具调用槽位补全（Slot Filling）纯函数层。

问题：用户说「我想请假」时，LLM 调 apply_leave 很容易缺日期/类型等必填槽位，
甚至编造参数直接发起审批。本模块在「chatbot 产生 tool_calls」与「工具执行/
审批挂起」之间加一道零成本预检：

- SLOT_SCHEMAS 登记各工具的必填槽位（中文标签用于反问文案）；
- check_tool_slots 返回缺失槽位标签列表（纯函数，零依赖可单测）；
- build_slot_hint 生成给 chatbot 的提示（经 ToolMessage 回传）：
  缺槽位时**不执行工具、不进审批**，由 chatbot 向用户反问收集，
  用户补充后下一轮携带完整信息再调工具（多轮合并由对话历史天然保证）。

接入位置：agent/nodes.py human_review_node（所有 tool_calls 的必经关卡，
图拓扑零改动，与请假参数/余额预检 _precheck_leave 同一模式）。
"""

# 必填槽位登记表：tool_name -> [(参数名, 中文标签), ...]
# 只登记「缺了就没法正确执行」的槽位；可选参数（如 apply_leave.reason）不登记。
SLOT_SCHEMAS: dict[str, list[tuple[str, str]]] = {
    "apply_leave": [
        ("uid", "请假员工 uid"),
        ("leave_type", "请假类型（年假/病假/事假）"),
        ("start_date", "开始日期（YYYY-MM-DD）"),
        ("end_date", "结束日期（YYYY-MM-DD）"),
    ],
    "generate_employment_certification": [
        ("uid", "员工 uid"),
        ("cer_type", "证明类型（employment 在职证明 / income 收入证明）"),
    ],
    "get_employee_profile": [
        ("uid", "员工 uid"),
    ],
    "get_leave_balance": [
        ("uid", "员工 uid"),
    ],
    "search_hr_policy": [
        ("query", "要检索的政策问题"),
    ],
}

# 值占位符：LLM 偶发输出形如「未知」「xxx」的占位值，视为缺失（不编造原则）
_PLACEHOLDER_VALUES = {"", "unknown", "未知", "null", "none", "n/a", "xxx", "?"}


def check_tool_slots(tool_name: str, args: dict | None) -> list[str]:
    """检查工具调用的必填槽位完整性，返回缺失槽位的中文标签列表（空 = 完整）。

    缺失判定：参数不存在、为 None、去空白后为空串、或命中占位值（大小写不敏感）。
    未登记的工具（schema 之外）一律返回 []，不影响放行。
    """
    schema = SLOT_SCHEMAS.get(tool_name)
    if not schema:
        return []
    args = args or {}
    missing: list[str] = []
    for param, label in schema:
        value = args.get(param)
        if value is None or str(value).strip().lower() in _PLACEHOLDER_VALUES:
            missing.append(label)
    return missing


def build_slot_hint(tool_name: str, missing: list[str]) -> str:
    """生成回传给 chatbot 的槽位提示（ToolMessage 内容）。

    口径：明确告知工具**未执行**、缺少哪些信息、下一步应该反问用户，
    且严禁编造参数值。chatbot 读后会生成自然语言反问（如
    「请问您请假的起止日期？年假、病假还是事假？」）。
    """
    missing_text = "、".join(missing)
    return (
        f"system:槽位预检未通过，工具 {tool_name} 未执行（也未进入审批）。"
        f"缺少必填信息：{missing_text}。"
        "请用自然语言向用户反问、收集这些缺失信息；在用户补充之前不要再次调用本工具，"
        "也绝不要编造或猜测参数值。若用户已在上一轮提供过部分信息，"
        "请结合对话历史合并，只追问仍缺失的部分。"
    )
