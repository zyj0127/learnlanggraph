# -*- coding: utf-8 -*-
"""多轮追问改写测试：启发式纯函数 + 假 LLM 链路 + human_review 接线。"""
import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.query_rewrite import (
    build_rewrite_prompt,
    format_history,
    needs_rewrite,
    rewrite_followup,
    rewrite_search_tool_calls,
)


class _FakeLLM:
    """假 LLM：记录 prompt，返回固定改写结果。"""

    def __init__(self, reply="病假的请假流程与天数规定是什么？"):
        self.reply = reply
        self.prompts = []
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        return type("Resp", (), {"content": self.reply})()


class _BoomLLM:
    def invoke(self, prompt):
        raise RuntimeError("LLM 不可用")


class TestNeedsRewrite(unittest.TestCase):
    """启发式触发策略（纯函数）。"""

    def test_first_turn_never_rewrites(self):
        # 首轮无历史：即使像追问也不改写（不多花 LLM 调用）
        self.assertFalse(needs_rewrite("那病假呢？", has_history=False))
        self.assertFalse(needs_rewrite("差旅住宿标准多少？", has_history=False))

    def test_followup_with_strong_marker(self):
        self.assertTrue(needs_rewrite("那病假呢？", has_history=True))
        self.assertTrue(needs_rewrite("这个怎么申请？", has_history=True))
        self.assertTrue(needs_rewrite("还有什么福利？", has_history=True))
        self.assertTrue(needs_rewrite("能换成线上办理吗？", has_history=True))

    def test_followup_with_weak_marker(self):
        self.assertTrue(needs_rewrite("审批要多久呢？", has_history=True))

    def test_very_short_without_topic(self):
        self.assertTrue(needs_rewrite("多少钱？", has_history=True))

    def test_self_contained_questions_skip(self):
        self.assertFalse(needs_rewrite("差旅住宿报销标准是多少？", has_history=True))
        self.assertFalse(needs_rewrite("病假能休几天", has_history=True))  # 含主题词且极短
        self.assertFalse(needs_rewrite("年假余额怎么查询", has_history=True))
        # 长且自足的问题不改写
        self.assertFalse(needs_rewrite(
            "请问一线城市出差的住宿报销上限和餐补分别是多少？", has_history=True))

    def test_empty_question(self):
        self.assertFalse(needs_rewrite("", has_history=True))
        self.assertFalse(needs_rewrite("   ", has_history=True))


class TestPromptAndHistory(unittest.TestCase):
    def test_format_history_filters_and_truncates(self):
        msgs = [
            HumanMessage(content="差旅住宿标准多少？"),
            AIMessage(content="一线城市 500 元/晚。"),
            ToolMessage(content="来源 [1]: ...", name="search_hr_policy",
                        tool_call_id="t1"),
            AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "t1"}]),
            HumanMessage(content="那病假呢？"),
        ]
        text = format_history(msgs)
        # 只保留真实问答轮：工具消息与纯工具调用消息不进入改写上下文
        self.assertIn("员工：差旅住宿标准多少？", text)
        self.assertIn("助手：一线城市 500 元/晚。", text)
        self.assertIn("员工：那病假呢？", text)
        self.assertNotIn("来源 [1]", text)

    def test_build_rewrite_prompt_contains_parts(self):
        p = build_rewrite_prompt("员工：差旅住宿标准多少？", "那病假呢？")
        self.assertIn("【对话历史】", p)
        self.assertIn("【最新提问】那病假呢？", p)
        self.assertIn("自足", p)


class TestRewriteFollowup(unittest.TestCase):
    HISTORY = [
        HumanMessage(content="差旅住宿标准多少？"),
        AIMessage(content="一线城市 500 元/晚[1]。"),
    ]

    def test_rewrite_hit(self):
        llm = _FakeLLM()
        out = rewrite_followup("那病假呢？", self.HISTORY, llm=llm)
        self.assertEqual(out, "病假的请假流程与天数规定是什么？")
        self.assertEqual(llm.calls, 1)
        # prompt 里既有历史又有最新提问
        self.assertIn("差旅住宿标准", llm.prompts[0])
        self.assertIn("那病假呢？", llm.prompts[0])

    def test_no_history_skips_llm(self):
        llm = _FakeLLM()
        out = rewrite_followup("那病假呢？", [], llm=llm)
        self.assertEqual(out, "那病假呢？")
        self.assertEqual(llm.calls, 0)

    def test_self_contained_skips_llm(self):
        llm = _FakeLLM()
        out = rewrite_followup("差旅住宿报销标准是多少？", self.HISTORY, llm=llm)
        self.assertEqual(out, "差旅住宿报销标准是多少？")
        self.assertEqual(llm.calls, 0)

    def test_llm_failure_falls_back(self):
        out = rewrite_followup("那病假呢？", self.HISTORY, llm=_BoomLLM())
        self.assertEqual(out, "那病假呢？")

    def test_bad_rewrite_not_adopted(self):
        # 空输出 / 超长跑题 / 与原句相同 → 一律回退原查询
        for reply in ("", "x" * 200, "那病假呢？"):
            out = rewrite_followup("那病假呢？", self.HISTORY, llm=_FakeLLM(reply=reply))
            self.assertEqual(out, "那病假呢？", reply[:20])


class TestRewriteSearchToolCalls(unittest.TestCase):
    """human_review 接线层：同 id AIMessage 替换契约。"""

    def _state(self, tool_calls):
        return [
            HumanMessage(content="差旅住宿标准多少？"),
            AIMessage(content="一线城市 500 元/晚[1]。"),
            HumanMessage(content="那病假呢？"),
            AIMessage(content="", tool_calls=tool_calls, id="ai-1"),
        ]

    def test_rewrites_query_and_keeps_id(self):
        tcs = [{"name": "search_hr_policy",
                "args": {"query": "那病假呢？"}, "id": "tc-1"}]
        out = rewrite_search_tool_calls(self._state(tcs), llm=_FakeLLM())
        self.assertIsNotNone(out)
        self.assertEqual(out.id, "ai-1")  # 同 id 替换（add_messages 去重更新）
        self.assertEqual(out.tool_calls[0]["args"]["query"],
                         "病假的请假流程与天数规定是什么？")
        self.assertEqual(out.tool_calls[0]["id"], "tc-1")

    def test_other_tools_untouched(self):
        tcs = [{"name": "get_employee_profile", "args": {"uid": "1001"}, "id": "tc-1"}]
        out = rewrite_search_tool_calls(self._state(tcs), llm=_FakeLLM())
        self.assertIsNone(out)  # 非检索工具：不改写、不替换

    def test_self_contained_query_no_replace(self):
        tcs = [{"name": "search_hr_policy",
                "args": {"query": "差旅住宿报销标准是多少？"}, "id": "tc-1"}]
        out = rewrite_search_tool_calls(self._state(tcs), llm=_FakeLLM())
        self.assertIsNone(out)

    def test_no_tool_calls_returns_none(self):
        out = rewrite_search_tool_calls([HumanMessage(content="你好")], llm=_FakeLLM())
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
