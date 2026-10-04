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


def judge_case(case: dict, tool_calls: list[dict]) -> dict:
    """按用例断言首轮 tool_calls，返回 {pass, failures}（纯函数）。"""
    expect = case["expect"]
    names = [tc.get("name", "") for tc in tool_calls]
    first = tool_calls[0] if tool_calls else None
    failures: list[str] = []

    if expect == "tool":
        if first is None:
            failures.append(f"应调用 {case['tool']} 但未调用任何工具")
        elif first.get("name") != case["tool"]:
            failures.append(f"工具选择错误：期望 {case['tool']}，实际 {first.get('name')}")
        else:
            args = {k: str(v).strip() for k, v in (first.get("args") or {}).items()}
            for key, want in (case.get("slots") or {}).items():
                got = args.get(key)
                if got is None:
                    failures.append(f"缺槽位 {key}（期望 {want}）")
                elif got != str(want).strip():
                    failures.append(f"槽位 {key} 不符：期望 {want}，实际 {got}")
    elif expect == "none":
        hit = [n for n in names if n in HR_DATA_TOOLS]
        if hit:
            failures.append(f"误触发 HR 数据工具：{hit}")
    elif expect == "clarify":
        # 通过 = 没调工具（先反问）；或调了但必填槽位缺失（槽位预检会兜底反问）
        if first is not None:
            from tools.slot_filling import check_tool_slots

            if not check_tool_slots(first.get("name", ""), first.get("args")):
                failures.append(f"信息不全却发起了完整调用：{first.get('name')} {first.get('args')}")

    return {"pass": not failures, "failures": failures}


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
            lambda r: r["tool_calls"] and r["tool_calls"][0].get("name") == r["case"]["tool"]),
        "slot_completeness": rate(
            [r for r in tool_rows if r["case"].get("slots")],
            lambda r: not any("槽位" in f or "缺槽位" in f for f in r["failures"])),
        "false_trigger_rate": rate(none_rows, lambda r: not r["pass"]),
        "clarify_accuracy": rate(clarify_rows, lambda r: r["pass"]),
        "pass_rate": rate(rows, lambda r: r["pass"]),
    }


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

    from langchain_core.messages import HumanMessage, SystemMessage

    from agent.nodes import ALL_TOOLS
    from config import get_chat_llm

    llm = get_chat_llm(temperature=0.0).bind_tools(ALL_TOOLS)
    rows: list[dict] = []
    for i, case in enumerate(cases):
        messages = [
            SystemMessage(content=_SYSTEM_TEMPLATE.format(uid=case.get("uid", ""))),
            HumanMessage(content=case["question"]),
        ]
        try:
            resp = llm.invoke(messages)
            tool_calls = [dict(tc) for tc in (getattr(resp, "tool_calls", None) or [])]
            error = None
        except Exception as exc:  # 单题失败不中断全量评测
            tool_calls, error = [], f"{type(exc).__name__}: {exc}"
        judged = judge_case(case, tool_calls)
        if error:
            judged["pass"] = False
            judged["failures"].append(f"LLM 调用异常：{error}")
        rows.append({"case": case, "tool_calls": tool_calls, "error": error,
                     **judged})
        print(f"[{i + 1}/{len(cases)}] {'PASS' if judged['pass'] else 'FAIL'} "
              f"{case['expect']:<7} {case['question'][:30]}", flush=True)

    report.update({"metrics": _metrics(rows), "rows": rows,
                   "ok": all(r["pass"] for r in rows)})
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
