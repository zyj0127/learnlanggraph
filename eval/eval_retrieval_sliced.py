# -*- coding: utf-8 -*-
"""检索评测分片执行器：与 evaluate.py 同一套口径，支持 --start/--limit 切片。

用途：评测集扩到 700+ 题后全管线（含 LLM 扩写）单轮约 50 分钟，超出单次执行
时限，故切片运行、逐片落盘 eval/report_part_{start}_{limit}.json，
全部跑完后用 --merge 合并为 eval/report.json。

用法：
    python -B -X utf8 eval/eval_retrieval_sliced.py --start 0 --limit 55
    python -B -X utf8 eval/eval_retrieval_sliced.py --merge
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import PROJECT_ROOT  # noqa: E402
from eval.dataset import DATASET_VERSION, POLICY_EVAL_SET  # noqa: E402
from eval.evaluate import _git_commit, _pipeline_rank, normalize  # noqa: E402


def run_slice(start: int, limit: int) -> None:
    from agent.rag_pipeline import retriever, search_hr_policy

    items = POLICY_EVAL_SET[start:start + limit]
    rows = []
    for i, item in enumerate(items):
        q, src = item["question"], item["expected_source"]
        docs = retriever.invoke(q)
        contents = [(d.page_content or "") for d in docs]
        base_hit = any(src in c for c in contents[:3])
        base_hit5 = any(src in c for c in contents[:5])
        base_rank = next((j for j, c in enumerate(contents, 1) if src in c), 0)
        merged = search_hr_policy.invoke(q)
        pipe_hit = normalize(src) in normalize(merged)
        pipe_rank = _pipeline_rank(merged, src)
        rows.append({"question": q, "baseline_hit": base_hit, "pipeline_hit": pipe_hit,
                     "baseline_hit5": base_hit5, "baseline_rank": base_rank,
                     "pipeline_rank": pipe_rank})
        print(f"  [{start + i}] 基线{'√' if base_hit else '×'}({base_rank or '-'})"
              f" 管线{'√' if pipe_hit else '×'} {q}", flush=True)

    out = PROJECT_ROOT / "eval" / f"report_part_{start}_{limit}.json"
    out.write_text(json.dumps({"start": start, "limit": limit, "rows": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"slice saved: {out} ({len(rows)} rows)")


def merge() -> None:
    valid_qs = {q["question"] for q in POLICY_EVAL_SET}
    total = len(valid_qs)
    parts = sorted((PROJECT_ROOT / "eval").glob("report_part_*.json"))
    rows_all, seen = [], set()
    for p in parts:
        data = json.loads(p.read_text(encoding="utf-8"))
        rows_all.extend(data["rows"])
    # 只计入当前评测集中仍存在的题目（支持剔除无效题后不重跑直接合并）
    rows = []
    for r in rows_all:
        if r["question"] in valid_qs and r["question"] not in seen:
            seen.add(r["question"])
            rows.append(r)
    dropped = len(rows_all) - len(rows)
    if dropped:
        print(f"合并时剔除 {len(rows_all) - len(rows)} 行（已不在当前评测集或重复）")
    if len(rows) != total:
        missing = total - len(rows)
        print(f"分片不完整：已收集 {len(rows)} / {total} 行（缺 {missing}），请先补齐分片再合并。")
        sys.exit(1)

    base_hits = sum(r["baseline_hit"] for r in rows)
    pipe_hits = sum(r["pipeline_hit"] for r in rows)
    base_hits5 = sum(r["baseline_hit5"] for r in rows)
    base_mrr = sum(1.0 / r["baseline_rank"] for r in rows if r["baseline_rank"])
    pipe_mrr = sum(1.0 / r["pipeline_rank"] for r in rows if r["pipeline_rank"])

    from agent.rag_pipeline import DEFAULT_HYBRID_WEIGHTS
    import os
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_version": DATASET_VERSION,
        "git_commit": _git_commit(),
        "hybrid_weights_env": os.getenv("HYBRID_WEIGHTS")
                              or f"默认 {tuple(DEFAULT_HYBRID_WEIGHTS)}",
        "retrieval": {
            "metric": "Hit@3 / Hit@5 / MRR（正确 chunk 的召回与排名质量）",
            "total": total,
            "baseline_hits": base_hits,
            "baseline_hit_rate": base_hits / total,
            "pipeline_hits": pipe_hits,
            "pipeline_hit_rate": pipe_hits / total,
            "baseline_hit5_rate": base_hits5 / total,
            "pipeline_hit5_rate": None,
            "pipeline_hit5_note": "全管线按设计只输出 Top-3 上下文，故不统计 Hit@5",
            "baseline_mrr": round(base_mrr / total, 4),
            "pipeline_mrr": round(pipe_mrr / total, 4),
            "note": "基线=纯混合检索(BM25+向量)；全管线=基线+LLM查询扩写+HyDE+CrossEncoder重排；分片执行后合并",
            "hybrid_weights": list(DEFAULT_HYBRID_WEIGHTS),
            "rows": rows,
        },
    }
    out = PROJECT_ROOT / "eval" / "report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"merged report -> {out}")
    print(f"基线 Hit@3 = {base_hits}/{total} = {base_hits/total:.1%} ｜ MRR = {base_mrr/total:.3f}")
    print(f"管线 Hit@3 = {pipe_hits}/{total} = {pipe_hits/total:.1%} ｜ MRR = {pipe_mrr/total:.3f}")
    print(f"基线 Hit@5 = {base_hits5}/{total} = {base_hits5/total:.1%}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--merge", action="store_true")
    a = ap.parse_args()
    if a.merge:
        merge()
    else:
        run_slice(a.start, a.limit or len(POLICY_EVAL_SET))
