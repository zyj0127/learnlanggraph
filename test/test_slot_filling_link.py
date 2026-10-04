# -*- coding: utf-8 -*-
"""槽位补全链路测试：反问回路 + 多轮合并发起审批（假 LLM，零真实 token）。

运行：python -m unittest test.test_slot_filling_link -v

设计：
- 节点级：直接调 human_review_node，构造带 tool_calls 的 AIMessage，
  断言缺槽位时回 ToolMessage 提示（不执行、不进审批）；
- 链路级：patch agent.nodes.get_llm_with_tools 为假 LLM（预置响应队列），
  驱动真实图（内存 checkpointer）走两轮：第一轮「我想请假」→ 槽位预检拦截 →
  chatbot 反问；第二轮补齐「周五一天年假」→ 完整槽位 → 成功挂起审批
  （证明多轮上下文合并成立）。零外部依赖环境由 conftest collect_ignore 跳过。
"""
import os
import unittest

os.environ.setdefault("LANGGRAPH_CHECKPOINTER", "memory")  # 不污染磁盘 checkpointer

from langchain_core.messages import AIMessage, ToolMessage

from agent.constants import IDLE_TIMEOUT_CMD  # noqa: F401（确保 langchain 链依赖可用）
from agent.nodes import human_review_node
from database.mock_db import init_db


def _ai_call(args: dict, call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": "apply_leave", "args": args, "id": call_id}])


class _FakeLLM:
    """预置响应队列的假 LLM：每次 invoke 弹出一条 AIMessage。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return self._responses.pop(0)


class HumanReviewSlotPrecheckTest(unittest.TestCase):
    """节点级：槽位预检在审批挂起之前生效。"""

    def test_missing_slots_returns_hint_no_interrupt(self):
        state = {"messages": [_ai_call({"uid": "1001"})],
                 "current_uid": "1001", "loop_state": 1}
        result = human_review_node(state)
        msgs = result["messages"]
        self.assertEqual(len(msgs), 1)
        self.assertIsInstance(msgs[0], ToolMessage)
        self.assertEqual(msgs[0].name, "apply_leave")
        self.assertIn("未执行", msgs[0].content)
        self.assertIn("请假类型", msgs[0].content)
        self.assertIn("开始日期", msgs[0].content)
        self.assertIn("反问", msgs[0].content)

    def test_mixed_calls_all_answered(self):
        """同一 AIMessage 多个 tool_call：缺槽位的回提示，完整的回暂缓（协议要求全应答）。"""
        ai = AIMessage(content="", tool_calls=[
            {"name": "apply_leave", "args": {"uid": "1001"}, "id": "c1"},
            {"name": "get_leave_balance", "args": {"uid": "1001"}, "id": "c2"},
        ])
        result = human_review_node({"messages": [ai], "current_uid": "1001"})
        msgs = result["messages"]
        self.assertEqual(len(msgs), 2)
        by_name = {m.name: m for m in msgs}
        self.assertIn("未执行", by_name["apply_leave"].content)
        self.assertIn("暂缓执行", by_name["get_leave_balance"].content)

    def test_complete_sensitive_call_proceeds_to_review(self):
        """槽位完整的敏感调用不被预检拦截（请假参数/余额预检通过则进入 interrupt）。

        interrupt 在无图上下文时抛异常即视为「走到了挂起点」（证明未被预检拦下）。
        """
        from langgraph.errors import GraphInterrupt

        init_db()  # 1001 年假 6 天，2 天申请预检可通过
        state = {"messages": [_ai_call({
            "uid": "1001", "leave_type": "年假",
            "start_date": "2026-11-02", "end_date": "2026-11-03",
            "reason": "链路测试"})], "current_uid": "1001", "loop_state": 1}
        with self.assertRaises((RuntimeError, GraphInterrupt)):
            human_review_node(state)


class SlotFillingLinkTest(unittest.TestCase):
    """链路级：两轮对话完成槽位补齐并发起审批。"""

    def setUp(self):
        init_db()

    def test_two_turn_slot_filling(self):
        from unittest.mock import patch

        from agent.graph_builder import hr_agent_app

        fake = _FakeLLM([
            # 轮 1 第一次：LLM 用残缺参数调 apply_leave（被槽位预检拦截）
            _ai_call({"uid": "1001", "leave_type": "年假"}),
            # 轮 1 第二次（收到 ToolMessage 提示后）：生成反问
            AIMessage(content="好的，请问您请假的起止日期是哪两天？"),
            # 轮 2：用户补充后，合并上下文发起完整调用
            _ai_call({"uid": "1001", "leave_type": "年假",
                      "start_date": "2026-11-06", "end_date": "2026-11-06",
                      "reason": "家中有事"}),
        ])
        config = {"configurable": {"thread_id": "slot-link-t1"}}

        with patch("agent.nodes.get_llm_with_tools", lambda: fake):
            # 轮 1：「我想请年假」→ 反问，不落 pending、不挂起审批
            out1 = hr_agent_app.invoke(
                {"messages": [("user", "我想请年假")], "current_uid": "1001"},
                config)
            last1 = out1["messages"][-1]
            self.assertIsInstance(last1, AIMessage)
            self.assertIn("日期", last1.content)          # 反问生效
            self.assertFalse(getattr(last1, "tool_calls", None))
            self.assertNotIn("__interrupt__", out1)        # 未进审批

            from database.repository import run_query

            rows = run_query(
                sql_pg="select count(*) c from leave_requests where status='pending'",
                sql_sqlite="select count(*) c from leave_requests where status='pending'")
            self.assertEqual(rows[0]["c"], 0)              # 未落任何 pending 工单

            # 轮 2：补充日期 → 完整槽位 → 成功挂起审批（多轮合并成立）
            out2 = hr_agent_app.invoke(
                {"messages": [("user", "2026-11-06 周五一天，家中有事")],
                 "current_uid": "1001"},
                config)
            self.assertIn("__interrupt__", out2)           # 进入审批挂起
            rows = run_query(
                sql_pg="select leave_type, start_date, end_date, status "
                       "from leave_requests where uid='1001' and status='pending'",
                sql_sqlite="select leave_type, start_date, end_date, status "
                           "from leave_requests where uid='1001' and status='pending'")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "pending")
            self.assertEqual(rows[0]["start_date"], "2026-11-06")


if __name__ == "__main__":
    unittest.main()
