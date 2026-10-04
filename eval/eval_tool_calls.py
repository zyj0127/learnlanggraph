# -*- coding: utf-8 -*-
"""工具调用准确率评测脚本（真实 LLM，needs_llm 场景）。

用法：
    python -m eval.eval_tool_calls --dry-run            # 只校验数据集结构（零依赖零成本）
    python -m eval.eval_tool_calls --limit 5            # 冒烟：只跑前 5 题（省钱）
    python -m eval.eval_tool_calls                      # 全量 70 题（CI nightly / 手动）

口径：
- 每题用「生产同款系统提示词 + 用户问题」单轮调用绑定全量工具的 LLM，
  只取第一次响应的 tool_calls（单轮首轮工具决策）；
- 判定逻辑见 judge_case（纯函数，可单测）；指标见 report 的 metrics 节；
- 结果落盘 eval/tool_call_report.json（含数据集版本、模型名、逐题明细）。
"""
import argparse
import json
import sys
import time
from pathlib import Path

from eval.tool_call_dataset import (
    HR_DATA_TOOLS,
    TOOL_CALL_DATASET_VERSION,
    TOOL_CALL_EVAL_SET,
    validate_dataset,
)

REPORT_PATH = Path(__file__).resolve().parent / "tool_call_report.json"

# 与 agent/nodes.py chatbot 系统提示词同口径（槽位收集指引版），uid 按题注入
_SYSTEM_TEMPLATE = (
    "你是飞羽科技的高级hr 智能助理。\n"
    "当前提问员工的 uid 是：{uid}。\n"
    "回答具体问题前，请务必先调用 get_employee_profile 获取该员工的工作属性，再基于事实作答。\n"
    "必须基于工具的返回事实，绝不能编造数字或条件。\n"
    "调用工具前必须确认必填参数齐全：例如请假需要请假类型（年假/病假/事假）、"
    "起止日期（YYYY-MM-DD）；开证明需要证明类型（在职/收入）。"
    "用户没提供时不要编造参数，先用自然语言追问收集（可多轮），"
    "结合对话历史合并已知信息，只追问仍缺失的部分；"
    "参数齐全后再一次性发起工具调用。"
)

# 预取型只读工具：系统提示词明确要求「回答前先调 get_employee_profile」，
# 因此首轮只调档案/余额是**符合设计的两段式行为**，不算工具选择错误——
# harness 会补一轮真实工具结果再看第二轮是否补调期望工具。
PREFETCH_TOOLS = {"get_employee_profile", "get_leave_balance"}

# 误触发判定名单：expect=none 时调了这些才算误触发。
# get_employee_profile 不在其中——系统提示词强制「先查档案」，政策题首轮
# 附带档案预取是提示词设计的副产物而非误判；真正要防的误触发是
# 查余额（不必要的个人数据）、开证明与请假（敏感写操作）。
MISFIRE_TOOLS = HR_DATA_TOOLS - {"get_employee_profile"}

# 回归阈值（基线 2026-10-27，DeepSeek 全量 70 题实测：pass_rate 1.0
# （其中确认追问式放行 10 题，显式计数）、false_trigger_rate 0.0）：
# 低于阈值即评测失败（exit 1），CI nightly 转红。
PASS_RATE_THRESHOLD = 0.90
FALSE_TRIGGER_THRESHOLD = 0.10


# confirm_ok 确认追问判定：槽位值在自然语言回复中的别名
# （cer_type 的枚举值 employment/income 在确认话术里以中文出现；uid 是系统
# 注入字段，从不出现在回复文本中，跳过匹配）
_CONFIRM_ALIASES = {"employment": "在职证明", "income": "收入证明"}


def judge_case(case: dict, tool_calls: list[dict], final_content: str = "") -> dict:
    """按用例断言 tool_calls（可为多轮合并序列），返回 {pass, failures}（纯函数）。

    判定语义：
    - tool：期望工具出现在**任一**调用中且槽位子集命中即通过
      （并行多调用与「先查档案再办事」的两段式都是合理行为——后者由 harness
      补轮后再判，多轮合并序列在此等价判定）。
      confirm_ok 用例（敏感写工具）：若最终未调用，但末轮回复以确认追问形式
      复述了全部断言槽位（类型/日期逐字出现），判通过并记 confirm 标记——
      敏感操作执行前向用户复述确认是人工审批之外的第二道防线，属安全加成，
      单独计数（metrics.confirm_assisted）以便观察占比，不静默混入。
    - none：出现 MISFIRE_TOOLS 中的调用即失败（档案预取不算误触发，见名单注释）；
    - clarify：出现**完整的敏感工具**（请假/开证明）调用才算失败——
      只读预取（档案/余额）不产生副作用，属于合理的信息收集；
      敏感工具槽位缺失的调用不算失败（槽位预检会兜底反问，行为安全）。
    """
    expect = case["expect"]
    names = [tc.get("name", "") for tc in tool_calls]
    failures: list[str] = []
    confirm_assisted = False

    if expect == "tool":
        target = next((tc for tc in tool_calls if tc.get("name") == case["tool"]), None)
        if target is not None:
            args = {k: str(v).strip() for k, v in (target.get("args") or {}).items()}
            for key, want in (case.get("slots") or {}).items():
                got = args.get(key)
                if got is None:
                    failures.append(f"缺槽位 {key}（期望 {want}）")
                elif got != str(want).strip():
                    failures.append(f"槽位 {key} 不符：期望 {want}，实际 {got}")
        elif case.get("confirm_ok") and (case.get("slots") or {}) and all(
                (_CONFIRM_ALIASES.get(str(w).strip(), str(w).strip())
                 in final_content)
                for k, w in case["slots"].items() if k != "uid"):
            # 确认追问式放行：未调用但完整复述了槽位（显式标记，不算工具命中）
            confirm_assisted = True
        elif not tool_calls:
            failures.append(f"应调用 {case['tool']} 但未调用任何工具")
        else:
            failures.append(f"工具选择错误：期望 {case['tool']}，实际 {names}")
    elif expect == "none":
        hit = [n for n in names if n in MISFIRE_TOOLS]
        if hit:
            failures.append(f"误触发 HR 数据工具：{hit}")
    elif expect == "clarify":
        from agent.constants import SENSITIVE_TOOLS
        from tools.slot_filling import check_tool_slots

        complete_sensitive = [
            tc for tc in tool_calls
            if tc.get("name") in SENSITIVE_TOOLS
            and not check_tool_slots(tc["name"], tc.get("args"))
        ]
        if complete_sensitive:
            tc = complete_sensitive[0]
            failures.append(
                f"信息不全却发起了完整敏感调用：{tc.get('name')} {tc.get('args')}")

    return {"pass": not failures, "failures": failures,
            "confirm_assisted": confirm_assisted}


def _metrics(rows: list[dict]) -> dict:
    """从逐题结果聚合四项指标。"""
    by = lambda e: [r for r in rows if r["case"]["expect"] == e]
    tool_rows, none_rows, clarify_rows = by("tool"), by("none"), by("clarify")

    def rate(items, pred):
        return round(sum(1 for r in items if pred(r)) / len(items), 4) if items else None

    return {
        "total": len(rows),
        "tool_selection_accuracy": rate(
            tool_rows,
            lambda r: any(t.get("name") == r["case"]["tool"]
                          for t in r["tool_calls"])),
        "slot_completeness": rate(
            [r for r in tool_rows if r["case"].get("slots")],
            lambda r: not any("槽位" in f or "缺槽位" in f for f in r["failures"])),
        "false_trigger_rate": rate(none_rows, lambda r: not r["pass"]),
        "clarify_accuracy": rate(clarify_rows, lambda r: r["pass"]),
        # 确认追问式放行题数（pass 但未实际调用工具，显式计数不静默）
        "confirm_assisted": sum(1 for r in rows if r.get("confirm_assisted")),
        "pass_rate": rate(rows, lambda r: r["pass"]),
    }


def _report_ok(metrics: dict) -> bool:
    """阈值制放行判定（纯函数）：pass_rate 与误触发率双达标才算绿。

    阈值常量见 PASS_RATE_THRESHOLD / FALSE_TRIGGER_THRESHOLD（含基线日期注释）。
    指标为 None（该组无题）时按通过处理。
    """
    pass_rate = metrics.get("pass_rate")
    ftr = metrics.get("false_trigger_rate")
    return (pass_rate is None or pass_rate >= PASS_RATE_THRESHOLD) and \
        (ftr is None or ftr <= FALSE_TRIGGER_THRESHOLD)


def _exec_prefetch(tool_call: dict) -> str:
    """执行预取型只读工具，返回真实结果文本（供第二轮上下文）。

    RBAC 旁路：本评测考的是工具调用决策而非鉴权（鉴权有独立测试覆盖），
    评测会话无登录身份，临时按 AUTH_ENABLED=false 口径放行；
    只允许 PREFETCH_TOOLS 白名单内的只读工具，敏感写工具绝不真实执行。
    """
    from unittest.mock import patch

    from tools import hr_tools

    fn = {"get_employee_profile": hr_tools.get_employee_profile,
          "get_leave_balance": hr_tools.get_leave_balance}[tool_call["name"]]
    args = {k: v for k, v in (tool_call.get("args") or {}).items() if k == "uid"}
    with patch("auth.guard._auth_enabled", return_value=False):
        return fn.invoke(args)  # StructuredTool：经 invoke 走标准调用面


def run(limit: int | None = None, dry_run: bool = False,
        output: Path = REPORT_PATH) -> dict:
    """跑评测并落盘报告；dry_run 只做数据集结构自检（不创建 LLM、不联网）。"""
    cases = TOOL_CALL_EVAL_SET[:limit] if limit else TOOL_CALL_EVAL_SET
    report: dict = {
        "dataset_version": TOOL_CALL_DATASET_VERSION,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dry_run": dry_run,
        "total_cases": len(cases),
    }

    if dry_run:
        problems = validate_dataset(cases)
        report["dataset_problems"] = problems
        report["ok"] = not problems
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        return report

    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    from agent.nodes import ALL_TOOLS
    from config import get_chat_llm

    llm = get_chat_llm(temperature=0.0).bind_tools(ALL_TOOLS)
    rows: list[dict] = []
    for i, case in enumerate(cases):
        messages = [
            SystemMessage(content=_SYSTEM_TEMPLATE.format(uid=case.get("uid", ""))),
            HumanMessage(content=case["question"]),
        ]
        rounds: list[list[dict]] = []
        final_content = ""
        try:
            # 两段式/预取链放行（显式理由）：系统提示词强制「先查档案再作答」，
            # 首轮（甚至前两轮）只调预取型只读工具是符合设计的行为——
            # 每轮补真实工具结果续跑，直到出现非预取调用或无调用，至多 3 轮；
            # 判定基于多轮合并序列 + 末轮回复文本（confirm_ok 确认追问判定）。
            for _round in range(3):
                resp = llm.invoke(messages)
                calls = [dict(tc) for tc in (getattr(resp, "tool_calls", None) or [])]
                rounds.append(calls)
                final_content = resp.content or ""
                names = {tc.get("name") for tc in calls}
                if (case["expect"] != "tool" or not calls
                        or case.get("tool") in names
                        or not names <= PREFETCH_TOOLS):
                    break
                messages.append(resp)  # 带 tool_calls 的 AIMessage 原样续上
                for tc in calls:
                    messages.append(ToolMessage(
                        content=_exec_prefetch(tc),
                        name=tc["name"], tool_call_id=tc["id"]))
            error = None
        except Exception as exc:  # 单题失败不中断全量评测
            rounds, error = [[]], f"{type(exc).__name__}: {exc}"
        tool_calls = [tc for rnd in rounds for tc in rnd]  # 多轮合并序列
        judged = judge_case(case, tool_calls, final_content=final_content)
        if error:
            judged["pass"] = False
            judged["failures"].append(f"LLM 调用异常：{error}")
        rows.append({"case": case, "tool_calls": tool_calls,
                     "rounds": len(rounds), "error": error, **judged})
        print(f"[{i + 1}/{len(cases)}] {'PASS' if judged['pass'] else 'FAIL'} "
              f"{case['expect']:<7} {case['question'][:30]}", flush=True)

    metrics = _metrics(rows)
    report.update({"metrics": metrics, "rows": rows,
                   "thresholds": {"pass_rate": PASS_RATE_THRESHOLD,
                                  "false_trigger_rate": FALSE_TRIGGER_THRESHOLD},
                   "ok": _report_ok(metrics)})
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                      encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="工具调用准确率评测（真实 LLM）")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 题（冒烟省钱）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只校验数据集结构，不调用 LLM")
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)

    report = run(limit=args.limit, dry_run=args.dry_run, output=args.output)
    if args.dry_run:
        n = len(report["dataset_problems"])
        print(f"dry-run：{report['total_cases']} 题，结构问题 {n} 个"
              + (f"：{report['dataset_problems']}" if n else ""))
    else:
        print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
        print(f"报告已落盘：{args.output}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
