# -*- coding: utf-8 -*-
"""
RAG 检索命中率 + 端到端事实准确率 + 超纲拒答 + 敏感审批 评测脚本
=================================================================

用法（在项目根目录下）：
    python eval/evaluate.py                # 跑全量（检索 + 端到端 + 拒答 + 审批）
    python eval/evaluate.py --retrieval    # 只跑检索命中率
    python eval/evaluate.py --end2end      # 只跑端到端准确率
    python eval/evaluate.py --refusal      # 只跑超纲拒答
    python eval/evaluate.py --approval     # 只跑敏感审批拦截

指标：
- 检索命中率 Hit@3：正确 chunk 是否进入 Top-3 召回（衡量 RAG 召回质量）
- 端到端事实准确率：最终回答是否覆盖关键事实（衡量整个 Agent 的效果）
- 幻觉拦截：审计节点打回重写次数及最终纠正率
- 超纲拒答率：知识库未收录问题是否转人工而非编造
- 审批拦截率：敏感证明开具是否 100% 触发人工审批挂起

输出：控制台表格 + eval/report.json
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# 评测用内存 checkpointer，避免读写生产库 db/checkpoints.db
os.environ.setdefault('LANGGRAPH_CHECKPOINTER', 'memory')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langchain_core.messages import HumanMessage  # noqa: E402
from langgraph.types import Command  # noqa: E402

from agent.constants import AUDIT_FAIL_PREFIX, HANDOFF_PREFIX  # noqa: E402
from eval.dataset import (  # noqa: E402
    APPROVAL_EVAL_SET,
    DATASET_VERSION,
    END_TO_END_EVAL_SET,
    OUT_OF_SCOPE_EVAL_SET,
    POLICY_EVAL_SET,
)
from observability import AUDIT_COUNTERS  # noqa: E402


def _git_commit() -> str:
    """记录本次评测对应的代码版本，保证报告可复现（无 git 环境返回 unknown）。"""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(PROJECT_ROOT), text=True, timeout=5,
        ).strip()
    except Exception:
        return "unknown"


def normalize(text: str) -> str:
    """去除所有空白，便于对中文/数字事实做鲁棒比对"""
    return re.sub(r"\s+", "", text or "")


def _new_state(question: str, uid: str) -> dict:
    return {
        "messages": [HumanMessage(content=question)],
        "current_uid": uid,
        "loop_state": 0,
    }


# ---------------------------------------------------------------------------
# 1. 检索命中率（Hit@3）
#    基线 = 纯混合检索；全管线 = 基线 + LLM 查询扩写 + HyDE + CrossEncoder 重排
# ---------------------------------------------------------------------------
def _pipeline_rank(merged: str, src: str) -> int:
    """从全管线返回文本中解析正确 chunk 的排名（1 起），未命中返回 0。

    返回文本格式：`来源 1: 章节 > 小节 \n 正文`，按 `来源 ` 切块即可还原排名。
    """
    if not merged:
        return 0
    blocks = merged.split("来源 ")
    for idx, block in enumerate(blocks[1:], 1):  # 第 0 块是前缀说明
        if normalize(src) in normalize(block):
            return idx
    return 0


def eval_retrieval(retriever, search_tool) -> dict:
    print("\n========== 检索命中率 Hit@3 / Hit@5 / MRR ==========")
    base_hits = pipe_hits = 0
    base_hits5 = pipe_hits5 = 0
    base_mrr = pipe_mrr = 0.0
    rows = []
    for item in POLICY_EVAL_SET:
        q = item["question"]
        src = item["expected_source"]

        # 基线：纯混合检索（BM25 + 向量）
        docs = retriever.invoke(q)
        contents = [(d.page_content or "") for d in docs]
        base_hit = any(src in c for c in contents[:3])
        base_hit5 = any(src in c for c in contents[:5])
        base_rank = next((i for i, c in enumerate(contents, 1) if src in c), 0)

        # 全管线：查询扩写 + HyDE + 重排后取 Top-3
        merged = search_tool.invoke(q)
        pipe_hit = normalize(src) in normalize(merged)
        pipe_rank = _pipeline_rank(merged, src)
        pipe_hit5 = pipe_rank > 0

        base_hits += int(base_hit)
        pipe_hits += int(pipe_hit)
        base_hits5 += int(base_hit5)
        pipe_hits5 += int(pipe_hit5)
        base_mrr += (1.0 / base_rank) if base_rank else 0.0
        pipe_mrr += (1.0 / pipe_rank) if pipe_rank else 0.0

        rows.append({
            "question": q, "baseline_hit": base_hit, "pipeline_hit": pipe_hit,
            "baseline_rank": base_rank, "pipeline_rank": pipe_rank,
        })
        print(f"  [基线{'√' if base_hit else '×'}({base_rank or '-'}) | 管线{'√' if pipe_hit else '×'}] {q}")

    total = len(POLICY_EVAL_SET)
    base_rate = base_hits / total if total else 0.0
    pipe_rate = pipe_hits / total if total else 0.0
    print(f"\n  命中率（基线混合检索）：{base_hits}/{total} = {base_rate:.1%} ｜ MRR = {base_mrr / total:.3f}")
    print(f"  命中率（全管线+重排）：{pipe_hits}/{total} = {pipe_rate:.1%} ｜ MRR = {pipe_mrr / total:.3f}")
    print(f"  Hit@5（基线）：{base_hits5}/{total} = {base_hits5 / total:.1%}"
          f" ｜ 全管线按设计只返回 Top-3，Hit@5 不适用")
    return {
        "metric": "Hit@3 / Hit@5 / MRR（正确 chunk 的召回与排名质量）",
        "total": total,
        "baseline_hits": base_hits,
        "baseline_hit_rate": base_rate,
        "pipeline_hits": pipe_hits,
        "pipeline_hit_rate": pipe_rate,
        "baseline_hit5_rate": base_hits5 / total if total else 0.0,
        "pipeline_hit5_rate": None,  # 管线只返回 Top-3，Hit@5 与 Hit@3 等同，不作为独立指标
        "pipeline_hit5_note": "全管线按设计只输出 Top-3 上下文，故不统计 Hit@5",
        "baseline_mrr": round(base_mrr / total, 4) if total else 0.0,
        "pipeline_mrr": round(pipe_mrr / total, 4) if total else 0.0,
        "note": "基线=纯混合检索(BM25+向量)；全管线=基线+LLM查询扩写+HyDE+CrossEncoder重排",
        "hybrid_weights": list(getattr(retriever, "weights", []) or []),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# 2. 端到端事实准确率（含幻觉审计拦截统计）
# ---------------------------------------------------------------------------
def eval_end_to_end(app) -> dict:
    print("\n========== 端到端事实准确率 ==========")
    AUDIT_COUNTERS.reset()
    passed_q = 0
    passed_fact = 0
    total_fact = 0
    audit_rejections = 0      # 审计打回次数（按题计：该题过程中发生过打回）
    audit_corrected = 0       # 被打回后最终仍通过的题数
    rows = []
    for i, item in enumerate(END_TO_END_EVAL_SET):
        q = item["question"]
        uid = item.get("uid", "1001")
        facts = item["required_facts"]
        config = {"configurable": {"thread_id": f"eval_e2e_{i}"}}
        # 兜底：若触发敏感审批则自动拒绝，避免评测挂起
        result = app.invoke(_new_state(q, uid), config)
        if app.get_state(config).next:
            result = app.invoke(Command(resume="reject"), config)

        # 幻觉审计：该题消息历史中是否出现过审计打回
        was_rejected = any(
            AUDIT_FAIL_PREFIX in (getattr(m, "content", "") or "")
            for m in result["messages"]
        )

        answer = normalize(result["messages"][-1].content)
        missing = [f for f in facts if normalize(f) not in answer]
        ok = not missing
        passed_q += int(ok)
        total_fact += len(facts)
        passed_fact += len(facts) - len(missing)
        audit_rejections += int(was_rejected)
        audit_corrected += int(was_rejected and ok)
        rows.append({
            "question": q, "facts": facts, "missing": missing,
            "audit_rejected": was_rejected, "pass": ok,
        })
        mark = "√" if ok else f"× 缺 {missing}"
        audit_mark = " [审计打回后纠正]" if was_rejected and ok else (" [审计打回仍未过]" if was_rejected else "")
        print(f"  [{mark}]{audit_mark} {q}")

    total_q = len(END_TO_END_EVAL_SET)
    acc = passed_q / total_q if total_q else 0.0
    fact_rate = passed_fact / total_fact if total_fact else 0.0
    print(f"\n  端到端准确率（问题级）：{passed_q}/{total_q} = {acc:.1%}")
    print(f"  端到端准确率（事实级）：{passed_fact}/{total_fact} = {fact_rate:.1%}")
    print(f"  幻觉审计：打回 {audit_rejections} 次，打回后最终纠正 {audit_corrected} 次")
    audit = AUDIT_COUNTERS.snapshot()
    print(f"  审计分层：规则层拦截 {audit['rule_blocked']} 次 ｜ 模型层拦截 {audit['llm_blocked']} 次 "
          f"｜ 解析失败 {audit['parse_errors']} 次 ｜ 转人工兜底 {audit['fallback_handoff']} 次")
    return {
        "metric": "事实准确率（最终回答覆盖关键事实）",
        "total_questions": total_q,
        "passed_questions": passed_q,
        "question_accuracy": acc,
        "total_facts": total_fact,
        "passed_facts": passed_fact,
        "fact_accuracy": fact_rate,
        "audit_rejections": audit_rejections,
        "audit_corrected": audit_corrected,
        # 审计分层指标（细节见 observability.AuditCounters）
        "audit_rule_blocked": audit["rule_blocked"],
        "audit_llm_blocked": audit["llm_blocked"],
        "audit_parse_errors": audit["parse_errors"],
        "audit_fallback_handoff": audit["fallback_handoff"],
        "audit_checked": audit["checked"],
        "audit_block_rate": audit["block_rate"],
        "audit_block_reasons": audit["block_reasons"],
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# 3. 超纲拒答：知识库未收录的问题应转人工，而不是编造政策
# ---------------------------------------------------------------------------
def eval_refusal(app) -> dict:
    print("\n========== 超纲拒答（转人工） ==========")
    passed = 0
    rows = []
    for i, item in enumerate(OUT_OF_SCOPE_EVAL_SET):
        q = item["question"]
        uid = item.get("uid", "1001")
        config = {"configurable": {"thread_id": f"eval_oos_{i}"}}
        result = app.invoke(_new_state(q, uid), config)
        if app.get_state(config).next:
            result = app.invoke(Command(resume="reject"), config)

        answer = result["messages"][-1].content or ""
        ok = HANDOFF_PREFIX in answer
        passed += int(ok)
        rows.append({"question": q, "handoff": ok, "answer_head": answer[:60]})
        print(f"  [{'√ 转人工' if ok else '× 未转接'}] {q}")

    total = len(OUT_OF_SCOPE_EVAL_SET)
    rate = passed / total if total else 0.0
    print(f"\n  超纲拒答率：{passed}/{total} = {rate:.1%}")
    return {
        "metric": "超纲拒答率（未收录问题转人工而非编造）",
        "total": total,
        "passed": passed,
        "refusal_rate": rate,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# 4. 敏感审批拦截：开具证明必须触发 Human-in-the-Loop 挂起
# ---------------------------------------------------------------------------
def eval_approval(app) -> dict:
    print("\n========== 敏感操作审批拦截 ==========")
    passed = 0
    rows = []
    for i, item in enumerate(APPROVAL_EVAL_SET):
        q = item["question"]
        uid = item.get("uid", "1001")
        config = {"configurable": {"thread_id": f"eval_appr_{i}"}}
        result = app.invoke(_new_state(q, uid), config)
        interrupted = bool(app.get_state(config).next)

        ok = interrupted
        note = "触发审批挂起"
        if not interrupted and item.get("allow_policy_refusal"):
            # P4 申请薪资证明：合规路径是引导工单而非生成，也视为通过
            answer = normalize(result["messages"][-1].content)
            ok = "工单" in answer or "无法" in answer
            note = "按政策拒绝并引导工单"

        if interrupted:
            # 清理挂起状态：统一拒绝，避免证明文件真的生成
            app.invoke(Command(resume="reject"), config)

        passed += int(ok)
        rows.append({"question": q, "uid": uid, "interrupted": interrupted, "pass": ok})
        print(f"  [{'√ ' + note if ok else '× 未拦截'}] {q} (uid={uid})")

    total = len(APPROVAL_EVAL_SET)
    rate = passed / total if total else 0.0
    print(f"\n  审批拦截率：{passed}/{total} = {rate:.1%}")
    return {
        "metric": "敏感操作审批拦截率（证明开具 100% 经人工审批）",
        "total": total,
        "passed": passed,
        "approval_rate": rate,
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description="RAG + 端到端 + 拒答 + 审批评测")
    parser.add_argument("--retrieval", action="store_true", help="仅跑检索命中率")
    parser.add_argument("--end2end", action="store_true", help="仅跑端到端准确率")
    parser.add_argument("--refusal", action="store_true", help="仅跑超纲拒答")
    parser.add_argument("--approval", action="store_true", help="仅跑敏感审批拦截")
    args = parser.parse_args()

    run_all = not (args.retrieval or args.end2end or args.refusal or args.approval)
    # 权重标注取代码里的真实默认值，避免报告头写死字符串、与实际配置产生漂移
    try:
        from agent.rag_pipeline import DEFAULT_HYBRID_WEIGHTS as _default_weights

        weights_label = os.getenv("HYBRID_WEIGHTS") or f"默认 {tuple(_default_weights)}"
    except Exception:
        weights_label = os.getenv("HYBRID_WEIGHTS") or "unknown"

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        # 可复现四件套：数据集版本 + 代码版本 + 权重配置 + 时间戳
        "dataset_version": DATASET_VERSION,
        "git_commit": _git_commit(),
        "hybrid_weights_env": weights_label,
    }

    if run_all or args.retrieval:
        from agent.rag_pipeline import retriever, search_hr_policy
        report["retrieval"] = eval_retrieval(retriever, search_hr_policy)

    app = None
    if run_all or args.end2end or args.refusal or args.approval:
        from agent.graph_builder import hr_agent_app
        app = hr_agent_app

    if run_all or args.end2end:
        report["end_to_end"] = eval_end_to_end(app)
    if run_all or args.refusal:
        report["out_of_scope_refusal"] = eval_refusal(app)
    if run_all or args.approval:
        report["sensitive_approval"] = eval_approval(app)

    out = PROJECT_ROOT / "eval" / "report.json"
    try:
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n评测报告已保存：{out}")
    except PermissionError:
        print(f"\n（无写权限，报告未落盘）评测报告内容如下：")
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
