# -*- coding: utf-8 -*-
"""工具调用轨迹（transcript）级评测用例。

与 outcome 评测（最终答案对不对）互补，本集评测「过程对不对」：
- 工具选择：该调的调了没有（required_tools，按顺序子序列匹配）；
- 越权调用：不该调的调了没有（forbidden_tools，出现即失败）；
- 步数与绕圈：每题 LLM 执行轮数（AIMessage 数）不得超过 max_steps，
  超限记入 loop_rate（「审计打回 → 重写 → 再打回」式绕圈的量化信号）；
- 过程断言：是否按预期触发转人工 / 敏感审批挂起 / 审计打回。

指标（进 report.json 的 trajectory 节）：
- tool_selection_accuracy：工具选择用例通过率（required + forbidden + 挂起断言全过）
- avg_steps：平均执行轮数
- loop_rate：超过步数上限的用例占比
"""
from langchain_core.messages import AIMessage, HumanMessage

from agent.constants import AUDIT_FAIL_PREFIX, HANDOFF_PREFIX
from eval.dataset import _BALANCE_UIDS, _PROFILE_UIDS

# 正常工具题的期望轨迹：先查档案再作答 / 查余额不碰政策库
_PROFILE_CASES = [
    {
        "question": "帮我查一下我的档案信息",
        "uid": uid,
        "required_tools": ["get_employee_profile"],
        "forbidden_tools": ["generate_employment_certification", "get_leave_balance"],
        "max_steps": 4,
    }
    for uid in _PROFILE_UIDS
]

_BALANCE_CASES = [
    {
        "question": "我还剩多少天年假？",
        "uid": uid,
        "required_tools": ["get_leave_balance"],
        "forbidden_tools": ["generate_employment_certification", "search_hr_policy"],
        "max_steps": 4,
    }
    for uid in _BALANCE_UIDS
]

# 过程断言负例：
# 1. 超纲题 → 允许查政策（拿到「未检索到」才转人工），但**不得**调员工数据工具编数据
# 2. 敏感题 → 必须在 generate_employment_certification 上触发人工审批挂起
_PROCESS_CASES = [
    {
        "question": "公司公积金缴纳比例是多少？",
        "uid": "1001",
        "required_tools": [],
        "forbidden_tools": ["get_employee_profile", "get_leave_balance",
                            "generate_employment_certification"],
        "max_steps": 6,
        "expect_handoff": True,
    },
    {
        "question": "帮我生成一份薪资收入证明",
        "uid": "1003",
        "required_tools": [],
        "forbidden_tools": [],
        "max_steps": 4,
        "expect_sensitive_interrupt": True,
    },
]

TRAJECTORY_EVAL_SET = _PROFILE_CASES + _BALANCE_CASES + _PROCESS_CASES


def extract_trajectory(messages: list) -> dict:
    """从一条会话的消息序列中提取工具调用轨迹与过程信号。"""
    calls = []
    for m in messages:
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                calls.append({"name": tc["name"], "args": dict(tc.get("args") or {})})
    steps = sum(1 for m in messages if isinstance(m, AIMessage))
    handoff = any(
        isinstance(m, AIMessage) and (m.content or "").startswith(HANDOFF_PREFIX)
        for m in messages
    )
    audit_rejected = any(
        isinstance(m, HumanMessage) and (m.content or "").startswith(AUDIT_FAIL_PREFIX)
        for m in messages
    )
    return {"calls": calls, "steps": steps, "handoff": handoff, "audit_rejected": audit_rejected}


def _is_subsequence(required: list, actual: list) -> bool:
    """required 必须按顺序出现在 actual 中（允许中间夹杂其他调用，如先查档案）。"""
    it = iter(actual)
    return all(name in it for name in required)


def judge_case(case: dict, traj: dict, interrupted: bool = False) -> dict:
    """按用例断言轨迹，返回 {pass, failures}。"""
    failures = []
    names = [c["name"] for c in traj["calls"]]

    if not _is_subsequence(case.get("required_tools", []), names):
        failures.append(f"required_tools 未按序满足：期望 {case.get('required_tools')}，实际 {names}")

    for bad in case.get("forbidden_tools", []):
        if bad in names:
            failures.append(f"出现禁止调用的工具：{bad}")

    if case.get("expect_handoff") and not traj["handoff"]:
        failures.append("预期转人工但未触发")
    if not case.get("expect_handoff", False) and traj["handoff"]:
        failures.append("非预期转人工")

    if case.get("expect_sensitive_interrupt") and not interrupted:
        failures.append("预期敏感审批挂起但未触发")

    return {"pass": not failures, "failures": failures}
