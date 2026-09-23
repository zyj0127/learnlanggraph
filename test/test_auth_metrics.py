# -*- coding: utf-8 -*-
"""授权审计周报聚合单测：auth_events 纳入 weekly_report / render_markdown 闭环。

运行：python -m unittest test.test_auth_metrics -v

零外部依赖：telemetry.sink 只依赖 config / agent.constants（均为轻量模块），
用临时目录的 telemetry.db（mock telemetry.sink.TELEMETRY_DB）验证聚合与渲染。
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from telemetry.sink import init_db, log_auth_event


class _TempTelemetryDb(unittest.TestCase):
    """脚手架：把埋点库指到临时目录，每个用例独立。"""

    def setUp(self):
        # Windows + WAL 模式下连接句柄释放有延迟，临时目录清理容忍残留（不影响断言）
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmpdir.cleanup)
        self.db_path = Path(self._tmpdir.name) / "telemetry.db"
        patcher = mock.patch("telemetry.sink.TELEMETRY_DB", self.db_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        init_db()


class TestAuthSecuritySummary(_TempTelemetryDb):
    def _seed(self):
        log_auth_event(action="view_profile", actor_uid="1001", actor_role="employee",
                       target_uid="1002", result="denied")
        log_auth_event(action="view_profile", actor_uid="1003", actor_role="employee",
                       target_uid="1001", result="denied")
        log_auth_event(action="view_leave", actor_uid="1001", actor_role="employee",
                       target_uid="1002", result="denied")
        log_auth_event(action="approval", actor_uid="hr001", actor_role="hr",
                       target_uid="1001", result="approved")
        log_auth_event(action="approval", actor_uid="hr001", actor_role="hr",
                       target_uid="1002", result="rejected")
        log_auth_event(action="approval", actor_uid="admin", actor_role="admin",
                       target_uid="hr001", result="denied", detail="self_approval")
        log_auth_event(action="cert_issued", actor_uid="hr001", actor_role="hr",
                       target_uid="1001", result="success", detail="income")

    def test_aggregation_counts(self):
        from telemetry.metrics import auth_security_summary

        self._seed()
        summary = auth_security_summary(days=7)
        self.assertEqual(summary["total"], 7)
        self.assertEqual(summary["access_denied_total"], 3)
        self.assertEqual(summary["approvals"], {"approved": 1, "rejected": 1, "denied": 1})
        self.assertEqual(summary["cert_issued"], 1)
        # 按事件类型/结果口径
        self.assertEqual(summary["by_action_result"]["view_profile/denied"], 2)
        self.assertEqual(summary["by_action_result"]["approval/approved"], 1)
        self.assertEqual(summary["by_action_result"]["cert_issued/success"], 1)
        # 按角色
        self.assertEqual(summary["by_role"], {"employee": 3, "hr": 3, "admin": 1})
        # 越权 Top 动作（降序）
        top = summary["access_denied_top_actions"]
        self.assertEqual(top[0], {"action": "view_profile", "count": 2})
        self.assertEqual(top[1], {"action": "view_leave", "count": 1})
        # 审批拦截（approval/denied）不计入越权拦截
        self.assertNotIn("approval", {item["action"] for item in top})

    def test_empty_table_returns_zero_structure(self):
        from telemetry.metrics import auth_security_summary

        summary = auth_security_summary(days=7)
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["access_denied_top_actions"], [])

    def test_missing_table_tolerated(self):
        """旧库无 auth_events 表（第一阶段产物）：按无安全事件处理，不报错。"""
        from telemetry.metrics import auth_security_summary

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("DROP TABLE auth_events")
        summary = auth_security_summary(days=7)
        self.assertEqual(summary["total"], 0)

    def test_weekly_report_includes_auth_security(self):
        from telemetry.metrics import weekly_report

        self._seed()
        report = weekly_report(days=7)
        self.assertIn("auth_security", report)
        self.assertEqual(report["auth_security"]["total"], 7)


class TestAuthSecurityRender(_TempTelemetryDb):
    def test_markdown_section_with_data(self):
        from telemetry.metrics import weekly_report
        from telemetry.report import render_markdown

        log_auth_event(action="view_profile", actor_uid="1001", actor_role="employee",
                       target_uid="1002", result="denied")
        log_auth_event(action="approval", actor_uid="hr001", actor_role="hr",
                       target_uid="1001", result="approved")
        md = render_markdown(weekly_report(days=7))
        self.assertIn("## 安全与审计", md)
        self.assertIn("授权事件总数：2", md)
        self.assertIn("越权拦截：1", md)
        self.assertIn("越权查档案（denied）", md)
        self.assertIn("员工 1", md)
        # 渲染不含目标 uid 等具体字段值以外的 PII（只出现计数与动作）
        self.assertNotIn("target_uid", md)

    def test_markdown_section_empty_fallback(self):
        from telemetry.metrics import weekly_report
        from telemetry.report import render_markdown

        md = render_markdown(weekly_report(days=7))
        self.assertIn("## 安全与审计", md)
        self.assertIn("本周期无安全事件", md)

    def test_render_without_auth_security_key(self):
        """旧调用方传入的 report dict 没有 auth_security 键时兜底渲染，不报错。"""
        from telemetry.report import render_markdown

        legacy = {
            "period_days": 7, "generated_at": "2026-02-15T00:00:00",
            "total_turns": 0, "unique_sessions": 0, "handoff_turns": 0,
            "handoff_rate": 0.0, "self_service_rate": 1.0, "audit_rejected_turns": 0,
            "by_intent": {}, "latency_p50_s": 0, "latency_p95_s": 0,
            "total_tokens": 0, "estimated_cost_rmb": 0.0, "top_badcases": [],
        }
        md = render_markdown(legacy)
        self.assertIn("本周期无安全事件", md)


if __name__ == "__main__":
    unittest.main()
