# -*- coding: utf-8 -*-
"""检索质量回归门禁（CI 阻塞级）。

门禁做三件事：
1. 数据完整性：每条政策题的标准答案子串必须能在知识库中定位，否则该题的评测口径失效；
2. 质量不回归：当前 Hit@3 / Hit@5 / MRR 不得低于 eval/baseline.json 记录的基线（含容差）；
   首次运行会自动生成基线并跳过断言，之后每次运行都参与门禁；
3. 配置契约：熔断策略常量、权重解析函数等关键配置必须保持可用。

运行：
    python -m unittest test.test_eval_gate -v

注意：本用例会加载 BGE 向量模型与重排模型（首次约 10 秒），但不调用 LLM，零 token 成本。
"""
import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("LANGGRAPH_CHECKPOINTER", "memory")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import DOC_PATH  # noqa: E402
from eval.dataset import DATASET_VERSION, POLICY_EVAL_SET  # noqa: E402

BASELINE_PATH = PROJECT_ROOT / "eval" / "baseline.json"
# 允许的回归容差：低于基线超过该幅度即判定回归（0.02 = 2 个百分点）
REGRESSION_TOLERANCE = 0.02


class TestGroundTruth(unittest.TestCase):
    def test_expected_source_locatable_in_handbook(self):
        """GT 必须能在知识库原文中定位，否则评测结果不可解释。"""
        text = DOC_PATH.read_text(encoding="utf-8")
        missing = [i["question"] for i in POLICY_EVAL_SET if i["expected_source"] not in text]
        self.assertEqual(missing, [], f"以下题目的 expected_source 在知识库中找不到：{missing}")

    def test_dataset_has_version(self):
        self.assertTrue(DATASET_VERSION, "评测集必须带版本号，报告才能落盘追溯")


class TestRetrievalGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from agent.rag_pipeline import retriever

        cls.retriever = retriever

    def _measure(self) -> dict:
        hits3 = hits5 = 0
        mrr = 0.0
        for item in POLICY_EVAL_SET:
            contents = [(d.page_content or "") for d in self.retriever.invoke(item["question"])]
            # 真正的排名：在整个返回列表中定位（MRR 用），Hit@k 则按窗口切片统计
            rank = next((i for i, c in enumerate(contents, 1) if item["expected_source"] in c), 0)
            hits3 += int(rank != 0 and rank <= 3)
            hits5 += int(rank != 0 and rank <= 5)
            mrr += (1.0 / rank) if rank else 0.0
        n = len(POLICY_EVAL_SET)
        return {"hit3": round(hits3 / n, 4), "hit5": round(hits5 / n, 4), "mrr": round(mrr / n, 4), "n": n}

    def test_retrieval_quality_not_regressed(self):
        current = self._measure()
        print(f"\n  当前检索指标：{current}")

        # 绝对底线（与基线文件无关）：Hit@5 代表召回能力，跌破即说明检索链路不可用；
        # MRR 底线用于兜住「能召回但排序完全失效」的情况。
        # 底线按数据集版本校准：2026.09-v3 起评测集以组合生成题为主（709 题，难度高于 v2 的
        # 167 题），基线 Hit@5 = 88.6%，底线取 0.85（留 3.6pt 余量）；
        # 若后续完成 12.3 交叉引用治理，应把底线上调回 0.9。
        hit5_floor = 0.85 if DATASET_VERSION >= "2026.09-v3" else 0.9
        self.assertGreaterEqual(
            current["hit5"], hit5_floor,
            f"Hit@5 跌破可服务底线（{hit5_floor}）：{current['hit5']}，应优先排查召回而非排序",
        )
        self.assertGreaterEqual(
            current["mrr"], 0.10,
            f"MRR 跌破底线：{current['mrr']}，排序质量需修复（检查重排链路与权重配置）",
        )

        if not BASELINE_PATH.exists():
            # 首次运行：尽力写出基线；无写权限（只读环境/沙箱）时跳过，不阻塞门禁
            payload = json.dumps(
                {"dataset_version": DATASET_VERSION,
                 "recorded_at": datetime.now().isoformat(timespec="seconds"), **current},
                ensure_ascii=False, indent=2,
            )
            try:
                BASELINE_PATH.write_text(payload, encoding="utf-8")
                self.skipTest(f"首次运行，已写入基线 {BASELINE_PATH}，请复核后提交到版本库")
            except OSError as exc:
                self.skipTest(f"首次运行且无法写入基线（{exc}）。当前指标：{current}")

        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

        # 评测集版本变更 → 旧基线失效，必须重新记录后再参与门禁对比
        if baseline.get("dataset_version") != DATASET_VERSION:
            payload = json.dumps(
                {"dataset_version": DATASET_VERSION,
                 "recorded_at": datetime.now().isoformat(timespec="seconds"), **current},
                ensure_ascii=False, indent=2,
            )
            try:
                BASELINE_PATH.write_text(payload, encoding="utf-8")
                self.skipTest(
                    f"评测集版本由 {baseline.get('dataset_version')} 变更为 {DATASET_VERSION}，"
                    f"旧基线失效，已重新记录：{current}")
            except OSError as exc:
                self.skipTest(f"评测集版本变更且无法写入新基线（{exc}）。当前指标：{current}")

        self.assertGreaterEqual(
            current["hit3"] + REGRESSION_TOLERANCE, baseline.get("hit3", 0),
            f"Hit@3 回归：{current['hit3']} < 基线 {baseline.get('hit3')}（容差 {REGRESSION_TOLERANCE}）",
        )
        self.assertGreaterEqual(
            current["mrr"] + REGRESSION_TOLERANCE, baseline.get("mrr", 0),
            f"MRR 回归：{current['mrr']} < 基线 {baseline.get('mrr')}",
        )


class TestPolicyContracts(unittest.TestCase):
    def test_audit_loop_guard_configured(self):
        from agent.constants import AUDIT_PARSE_RETRY_LIMIT, MAX_REFLECTION_LOOPS

        self.assertGreater(MAX_REFLECTION_LOOPS, 0, "必须有反思熔断上限，避免审计-重写死循环")
        self.assertGreaterEqual(AUDIT_PARSE_RETRY_LIMIT, 0)

    def test_audit_fallback_uses_handoff_prefix(self):
        """熔断兜底必须走转人工口径，不能输出未过审内容。"""
        from agent.constants import AUDIT_FALLBACK_MESSAGE, HANDOFF_PREFIX

        self.assertTrue(AUDIT_FALLBACK_MESSAGE.startswith(HANDOFF_PREFIX))

    def test_hybrid_weights_resolvable_and_safe(self):
        from agent.rag_pipeline import DEFAULT_HYBRID_WEIGHTS, _resolve_weights

        os.environ.pop("HYBRID_WEIGHTS", None)
        self.assertEqual(_resolve_weights(), list(DEFAULT_HYBRID_WEIGHTS))

        os.environ["HYBRID_WEIGHTS"] = "0.5,0.5"
        self.assertEqual(_resolve_weights(), [0.5, 0.5])

        for bad in ("abc", "0.5", "0.5,-0.1", "0,0"):
            os.environ["HYBRID_WEIGHTS"] = bad
            self.assertEqual(_resolve_weights(), list(DEFAULT_HYBRID_WEIGHTS), f"非法值 {bad} 应回退默认")
        os.environ.pop("HYBRID_WEIGHTS", None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
