# -*- coding: utf-8 -*-
"""槽位补全纯函数 + 工具调用评测集/dry-run 单测（零外部依赖可跑）。

运行：python -m unittest test.test_slot_filling -v
"""
import json
import tempfile
import unittest
from pathlib import Path

from eval.eval_tool_calls import judge_case, run
from eval.tool_call_dataset import (
    HR_DATA_TOOLS,
    TOOL_CALL_EVAL_SET,
    validate_dataset,
)
from tools.slot_filling import (
    SLOT_SCHEMAS,
    build_slot_hint,
    check_tool_slots,
)


class CheckToolSlotsTest(unittest.TestCase):
    def test_complete_apply_leave(self):
        args = {"uid": "1001", "leave_type": "年假",
                "start_date": "2026-11-02", "end_date": "2026-11-03"}
        self.assertEqual(check_tool_slots("apply_leave", args), [])

    def test_missing_each_slot(self):
        full = {"uid": "1001", "leave_type": "年假",
                "start_date": "2026-11-02", "end_date": "2026-11-03"}
        for key in full:
            args = {k: v for k, v in full.items() if k != key}
            missing = check_tool_slots("apply_leave", args)
            self.assertEqual(len(missing), 1, f"缺 {key} 应检出 1 项")

    def test_empty_and_placeholder_values(self):
        for bad in ("", "  ", "未知", "unknown", "None", "xxx"):
            missing = check_tool_slots("apply_leave", {
                "uid": "1001", "leave_type": bad,
                "start_date": "2026-11-02", "end_date": "2026-11-03"})
            self.assertEqual(len(missing), 1, f"占位值 {bad!r} 应视为缺失")

    def test_reason_is_optional(self):
        """reason 不登记为必填槽位：缺 reason 不影响完整性。"""
        args = {"uid": "1001", "leave_type": "年假",
                "start_date": "2026-11-02", "end_date": "2026-11-03"}
        self.assertEqual(check_tool_slots("apply_leave", args), [])

    def test_cert_and_read_tools(self):
        self.assertEqual(check_tool_slots(
            "generate_employment_certification",
            {"uid": "1001", "cer_type": "income"}), [])
        self.assertEqual(len(check_tool_slots(
            "generate_employment_certification", {"uid": "1001"})), 1)
        self.assertEqual(check_tool_slots("get_employee_profile", {"uid": "1001"}), [])
        self.assertEqual(len(check_tool_slots("get_leave_balance", {})), 1)

    def test_unknown_tool_passes(self):
        self.assertEqual(check_tool_slots("no_such_tool", {}), [])
        self.assertEqual(check_tool_slots("no_such_tool", None), [])

    def test_hint_content(self):
        hint = build_slot_hint("apply_leave", ["开始日期（YYYY-MM-DD）"])
        self.assertIn("未执行", hint)
        self.assertIn("开始日期", hint)
        self.assertIn("不要再次调用", hint)
        self.assertIn("绝不要编造", hint)


class ToolCallDatasetTest(unittest.TestCase):
    def test_dataset_valid(self):
        self.assertEqual(validate_dataset(), [])

    def test_dataset_scale_and_mix(self):
        n = len(TOOL_CALL_EVAL_SET)
        self.assertTrue(50 <= n <= 80)
        expects = {c["expect"] for c in TOOL_CALL_EVAL_SET}
        self.assertEqual(expects, {"tool", "none", "clarify"})

    def test_judge_tool_hit_and_slot_subset(self):
        case = {"question": "q", "uid": "1001", "expect": "tool",
                "tool": "apply_leave",
                "slots": {"leave_type": "年假", "start_date": "2026-11-02"}}
        ok = judge_case(case, [{"name": "apply_leave", "args": {
            "uid": "1001", "leave_type": "年假", "start_date": "2026-11-02",
            "end_date": "2026-11-03"}}])
        self.assertTrue(ok["pass"])
        bad_tool = judge_case(case, [{"name": "get_leave_balance", "args": {}}])
        self.assertFalse(bad_tool["pass"])
        bad_slot = judge_case(case, [{"name": "apply_leave", "args": {
            "leave_type": "病假", "start_date": "2026-11-02"}}])
        self.assertFalse(bad_slot["pass"])

    def test_judge_none_and_clarify(self):
        none_case = {"question": "你好", "expect": "none"}
        self.assertTrue(judge_case(none_case, [])["pass"])
        self.assertFalse(judge_case(
            none_case, [{"name": "get_employee_profile", "args": {"uid": "1001"}}])["pass"])

        clarify = {"question": "我想请假", "expect": "clarify"}
        self.assertTrue(judge_case(clarify, [])["pass"])  # 先反问：通过
        # 调了但缺槽位（预检会兜底）：通过
        self.assertTrue(judge_case(clarify, [{"name": "apply_leave", "args": {
            "uid": "1001", "leave_type": "年假"}}])["pass"])
        # 信息不全却补齐式编造完整调用：不通过
        self.assertFalse(judge_case(clarify, [{"name": "apply_leave", "args": {
            "uid": "1001", "leave_type": "年假",
            "start_date": "2026-11-02", "end_date": "2026-11-03"}}])["pass"])

    def test_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "r.json"
            report = run(dry_run=True, output=out)
            self.assertTrue(report["ok"])
            self.assertEqual(report["dataset_problems"], [])
            saved = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(saved["dry_run"])
            self.assertEqual(saved["total_cases"], len(TOOL_CALL_EVAL_SET))


if __name__ == "__main__":
    unittest.main()
