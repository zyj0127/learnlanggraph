# -*- coding: utf-8 -*-
"""
本地可观测性基准：跑一批代表性问答，统计 token 用量、延迟、成本。

用法（在项目根目录下）：
    python eval/benchmark.py

输出：控制台 JSON + eval/benchmark_report.json
"""
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("LANGGRAPH_CHECKPOINTER", "memory")  # 评测用内存版，不污染生产库

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langchain_core.messages import HumanMessage  # noqa: E402
from observability import UsageTracker  # noqa: E402
from eval.dataset import END_TO_END_EVAL_SET  # noqa: E402


def main():
    from agent.graph_builder import hr_agent_app

    tracker = UsageTracker()
    total_start = time.perf_counter()
    per_question = []

    print(f"开始基准测试，共 {len(END_TO_END_EVAL_SET)} 道题…\n")
    for i, item in enumerate(END_TO_END_EVAL_SET):
        q = item["question"]
        uid = item.get("uid", "1001")
        t0 = time.perf_counter()
        hr_agent_app.invoke(
            {"messages": [HumanMessage(content=q)], "current_uid": uid, "loop_state": 0},
            {"callbacks": [tracker], "configurable": {"thread_id": f"bench_{i}"}},
        )
        dt = time.perf_counter() - t0
        per_question.append({"question": q, "latency_s": round(dt, 3)})
        print(f"  [{dt:6.2f}s] {q}")

    total_time = time.perf_counter() - total_start
    usage = tracker.summary()
    report = {
        "questions": len(END_TO_END_EVAL_SET),
        "total_wall_time_s": round(total_time, 2),
        "avg_end_to_end_latency_s": round(total_time / len(END_TO_END_EVAL_SET), 3),
        "usage": usage,
        "per_question": per_question,
    }

    print("\n========== 汇总 ==========")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    out = PROJECT_ROOT / "eval" / "benchmark_report.json"
    try:
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已保存：{out}")
    except PermissionError:
        print("\n（无写权限，报告未落盘，内容见上方 JSON）")


if __name__ == "__main__":
    main()
