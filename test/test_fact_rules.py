# -*- coding: utf-8 -*-
"""审计规则层单测：数字类幻觉必须被拦下，正常复述不得误杀。

运行：python -m unittest test.test_fact_rules -v
（纯规则单测，不加载模型、不调用 LLM，秒级完成，适合放进 CI 冒烟）
"""
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.fact_rules import check_numbers, extract_facts  # noqa: E402


class TestExtractFacts(unittest.TestCase):
    def test_extracts_units_and_rank(self):
        facts = dict(extract_facts("P5 员工每年享有 9 天年假，餐补 120 元/天，上浮 20%。"))
        self.assertEqual(facts.get("天数"), "9")
        self.assertEqual(facts.get("金额"), "120")
        self.assertEqual(facts.get("百分比"), "20")
        self.assertEqual(facts.get("职级"), "5")

    def test_ignores_plain_numbers_projects_and_dates(self):
        """条款编号、年份、日期这类无单位数字不参与比对，避免误杀。"""
        self.assertEqual(extract_facts("见 2.5 条款，2026 年 1 月 1 日生效"), [])

    def test_month_duration_requires_unit(self):
        """「N 个月」才算月数，裸「N 月」是日期。"""
        self.assertIn(("月数", "6"), extract_facts("入职 6 个月"))
        self.assertNotIn(("月数", "1"), extract_facts("2026 年 1 月生效"))

    def test_normalizes_decimal(self):
        self.assertIn(("天数", "0.5"), extract_facts("每满 1 个月享有 0.5 天"))


class TestCheckNumbers(unittest.TestCase):
    def test_blocks_fabricated_amount(self):
        context = "P4 - P5：一线城市住宿上限 450 元/天，二线城市住宿上限 300 元/天"
        answer = "P4 员工去北京出差，住宿每天最多报销 500 元。"
        violations = check_numbers(answer, context, "P4 员工去北京出差住宿能报多少？")
        self.assertTrue(any("500" in v for v in violations), violations)

    def test_blocks_fabricated_days(self):
        context = "P5 至 P7 职级：入职满 3 年及以上者，享有年假 14 天"
        answer = "P6 入职四年的员工，年假有 18 天。"
        violations = check_numbers(answer, context, "P6 入职四年的老员工，年假多少天？")
        self.assertTrue(any("18" in v for v in violations), violations)

    def test_allows_restated_facts(self):
        context = "P4 及以下职级：入职满 1 年不满 3 年者，享有年假 5 天"
        answer = "P4 职级、入职满两年的员工，企业福利年假有 5 天。"
        self.assertEqual(check_numbers(answer, context, "P4 职级、入职满两年的员工，企业福利年假有几天？"), [])

    def test_allows_numbers_from_question(self):
        """用户提问里的数字是白名单，避免「员工问 5 天，回答复述 5 天」被误判。"""
        context = "3天以内的常规假期由直属主管审批即可"
        answer = "员工请 5 天假需要二级审批。"
        violations = check_numbers(answer, context, "员工请 5 天假，审批流程是什么？")
        self.assertEqual([v for v in violations if "5" in v], [])

    def test_ignores_section_references(self):
        context = "报销时限见 2.5 条款，费用发生后 60 个自然日内提交"
        answer = "根据 2.5 条款，报销需在 60 个自然日内提交。"
        self.assertEqual(check_numbers(answer, context, "报销多久内提交？"), [])

    def test_no_context_returns_empty(self):
        """没有 RAG 原文可对照时规则层不介入（工具类回答走其它校验路径）。"""
        self.assertEqual(check_numbers("随便 999 元", "", ""), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
