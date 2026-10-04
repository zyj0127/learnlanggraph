# -*- coding: utf-8 -*-
"""流式幻觉预检测试：切分边界 / 命中熔断 / 透传 / 不双计 / 开关旁路。

全部假图假流，零 LLM、零模型依赖。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.stream_guard import StreamFactGuard, mark_stream_blocked, was_stream_blocked
from observability import AUDIT_COUNTERS

RAG_CONTEXT = "来源 [1]: 1. 考勤与假期管理制度 > 1.5 病假与医疗期\n病假每年 10 天，需医院证明。"
QUESTION = "病假能休几天？"


class TestBoundarySplitting(unittest.TestCase):
    """切分策略：只在句边界触发，半个数字不误判。"""

    def _guard(self):
        g = StreamFactGuard(QUESTION)
        g.set_context(RAG_CONTEXT)
        return g

    def test_half_number_not_judged(self):
        # 「10」逐字符流入：未到句边界，永不触发（「1」不该被判幻觉）
        g = self._guard()
        for tok in ("病假", "每年", " 1", "0", " 天"):
            self.assertIsNone(g.feed(tok))
        self.assertIsNone(g.flush())  # 终检：10 在原文中，合法

    def test_wrong_number_mid_stream_safe_until_boundary(self):
        # 错误数字「15」：流动途中（无边界）不触发，句边界一到立即命中
        g = self._guard()
        self.assertIsNone(g.feed("病假每年 1"))
        self.assertIsNone(g.feed("5 天"))          # 仍无句边界，不判
        self.assertIsNone(g.feed("，需要医院证明"))  # 「，」不是句边界
        r = g.feed("。")                           # 句边界触发 → 命中
        self.assertIsNotNone(r)
        self.assertIn("15", r)
        self.assertTrue(g.aborted)

    def test_correct_number_passes(self):
        g = self._guard()
        for tok in ("病假", "每年 10 天", "。"):
            self.assertIsNone(g.feed(tok))
        self.assertFalse(g.aborted)

    def test_no_digit_segment_skipped(self):
        g = self._guard()
        self.assertIsNone(g.feed("需要提供医院证明。"))
        self.assertIsNone(g.feed("门诊即可。"))
        self.assertFalse(g.aborted)

    def test_multi_sentence_checks_prefix_incrementally(self):
        # 第一句正确 → 放行；第二句错误数字 → 在第二个句边界命中
        g = self._guard()
        self.assertIsNone(g.feed("病假每年 10 天。"))
        r = g.feed("事假每年 50 天。")
        self.assertIsNotNone(r)
        self.assertIn("50", r)

    def test_flush_catches_trailing_bad_number(self):
        # 答案无句边界收尾：终检兜底
        g = self._guard()
        g.feed("病假每年 99 天")
        r = g.flush()
        self.assertIsNotNone(r)
        self.assertIn("99", r)

    def test_no_context_no_check(self):
        g = StreamFactGuard(QUESTION)  # 未 set_context（非政策问答）
        self.assertIsNone(g.feed("随便 999 天。"))
        self.assertIsNone(g.flush())
        self.assertFalse(g.aborted)

    def test_disabled_bypasses(self):
        g = StreamFactGuard(QUESTION, enabled=False)
        g.set_context(RAG_CONTEXT)
        self.assertIsNone(g.feed("病假每年 99 天。"))
        self.assertIsNone(g.flush())
        self.assertFalse(g.aborted)


class TestStreamBlockedRegistry(unittest.TestCase):
    def test_mark_and_query(self):
        mark_stream_blocked("测试问题-登记")
        self.assertTrue(was_stream_blocked("测试问题-登记"))
        self.assertFalse(was_stream_blocked("别的问题"))


class _FakeAppStream:
    """假图：先发检索 ToolMessage，再逐段流出含幻觉数字的回答。"""

    def __init__(self, chunks, context=RAG_CONTEXT):
        self.chunks = chunks
        self.context = context

    def stream(self, graph_input, config, stream_mode=None):
        yield (ToolMessage(content=self.context, name="search_hr_policy",
                           tool_call_id="t1"), {"langgraph_node": "tools"})
        for c in self.chunks:
            yield (AIMessage(content=c), {"langgraph_node": "chatbot"})

    def get_state(self, config):
        final = AIMessage(content="".join(self.chunks))
        return SimpleNamespace(next=None, values={
            "messages": [HumanMessage(content=QUESTION),
                         ToolMessage(content=self.context, name="search_hr_policy",
                                     tool_call_id="t1"),
                         final]})


class TestRunnerIntegration(unittest.TestCase):
    """session_runner 事件契约：命中熔断话术 / 未命中透传 / 开关旁路 / 不双计。"""

    def setUp(self):
        AUDIT_COUNTERS.reset()
        # 流式拦截登记是进程级注册表（TTL 10 分钟），用例间必须清理防串扰
        from agent import stream_guard

        with stream_guard._BLOCKED_LOCK:
            stream_guard._BLOCKED.clear()

    def _run(self, app, question=QUESTION):
        from agent import session_runner

        config = {"configurable": {"thread_id": "t-stream"}}
        meta = {"question": question, "uid": "1001", "channel": "http"}
        with patch("agent.semantic_cache.cache_lookup", return_value=None), \
                patch("agent.session_runner.log_turn", return_value=1), \
                patch("agent.semantic_cache.cache_store", return_value=True):
            return list(session_runner._stream_turn_impl(
                app, {"messages": []}, config, meta=meta))

    def test_bad_number_aborts_with_fallback(self):
        app = _FakeAppStream(["病假每年 ", "15 天", "，请知悉", "。"])
        events = self._run(app)
        tokens = "".join(e["content"] for e in events if e["type"] == "token")
        from agent.constants import AUDIT_FALLBACK_MESSAGE

        # 检出点（句边界「。」）之后停止流出：触发 token 及其后内容不透出，
        # 前缀之后紧接兜底话术（检出点前已流出的部分属预期，切分策略文档写明）
        self.assertEqual(tokens, "病假每年 15 天，请知悉" + AUDIT_FALLBACK_MESSAGE)
        self.assertEqual(events[-1]["type"], "done")
        # 计数：规则层 +1（流式口径，reason 带流式前缀）
        snap = AUDIT_COUNTERS.snapshot()
        self.assertEqual(snap["rule_blocked"], 1)
        self.assertIn("流式", snap["block_reasons"][0]["reason"])
        # 登记：事后审计可据此去重
        self.assertTrue(was_stream_blocked(QUESTION))

    def test_good_answer_passes_through(self):
        app = _FakeAppStream(["病假每年 ", "10 天", "，需医院证明", "。"])
        events = self._run(app)
        tokens = "".join(e["content"] for e in events if e["type"] == "token")
        self.assertEqual(tokens, "病假每年 10 天，需医院证明。")
        self.assertEqual(AUDIT_COUNTERS.snapshot()["rule_blocked"], 0)
        self.assertFalse(was_stream_blocked(QUESTION))

    def test_switch_off_bypasses(self):
        app = _FakeAppStream(["病假每年 ", "15 天", "。"])
        # 真实 Settings 实例临时关闭开关（不全局 patch，避免波及其他 get_settings 调用方）
        from config import get_settings

        settings = get_settings()
        old = settings.stream_fact_check_enabled
        settings.stream_fact_check_enabled = False
        try:
            events = self._run(app)
        finally:
            settings.stream_fact_check_enabled = old
        tokens = "".join(e["content"] for e in events if e["type"] == "token")
        self.assertEqual(tokens, "病假每年 15 天。")  # 完整透传
        self.assertEqual(AUDIT_COUNTERS.snapshot()["rule_blocked"], 0)

    def test_no_double_count_with_post_audit(self):
        """与事后审计并存不双计：流式命中后 fact_check 规则层跳过计数。"""
        from agent.nodes import fact_check_node

        # 先模拟流式命中（登记 + 计数一次）
        mark_stream_blocked(QUESTION)
        AUDIT_COUNTERS.record_block("rule", "[流式预检] 天数 15 未在知识库原文中出现")
        before = AUDIT_COUNTERS.snapshot()["rule_blocked"]

        bad_answer = "病假每年 15 天。"
        state = {"messages": [
            HumanMessage(content=QUESTION),
            ToolMessage(content=RAG_CONTEXT, name="search_hr_policy",
                        tool_call_id="t1"),
            AIMessage(content=bad_answer)], "loop_state": 0}
        out = fact_check_node(state)  # 规则层仍会打回（行为不变）
        self.assertTrue(out["messages"])  # 打回消息存在
        # 但计数不增加（同轮同一幻觉不双计）
        self.assertEqual(AUDIT_COUNTERS.snapshot()["rule_blocked"], before)

    def test_post_audit_counts_normally_without_stream_block(self):
        """未被流式拦截的轮次，事后审计照常计数。"""
        from agent.nodes import fact_check_node

        state = {"messages": [
            HumanMessage(content=QUESTION),
            ToolMessage(content=RAG_CONTEXT, name="search_hr_policy",
                        tool_call_id="t1"),
            AIMessage(content="病假每年 15 天。")], "loop_state": 0}
        fact_check_node(state)
        self.assertEqual(AUDIT_COUNTERS.snapshot()["rule_blocked"], 1)


if __name__ == "__main__":
    unittest.main()
