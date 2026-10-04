# -*- coding: utf-8 -*-
"""引用溯源单测：来源编号/组装/解析纯函数 + 旧格式解析兼容 + sources 事件契约。

运行：python -m unittest test.test_citations -v

分层：
- 纯函数部分（number_sources / format_sources_text / parse_sources_from_text，
  组装与解析互逆）零依赖可跑；
- sources 事件契约（session_runner 假图驱动）依赖 langchain（ToolMessage），
  缺依赖时 skipUnless 跳过（不整模块 ignore，保住纯函数部分零依赖可跑）。
"""
import importlib.util
import unittest
from types import SimpleNamespace

from agent.citations import (
    format_sources_text,
    number_sources,
    parse_sources_from_text,
)

_HAS_LANGCHAIN = importlib.util.find_spec("langchain_core") is not None


def _doc(content: str, chapter: str = "第7章 差旅", section: str = "7.1 总则"):
    return SimpleNamespace(page_content=content,
                           metadata={"Chapter": chapter, "Section": section})


class NumberSourcesTest(unittest.TestCase):
    def test_stable_numbering(self):
        docs = [_doc("甲"), _doc("乙"), _doc("丙")]
        sources = number_sources(docs)
        self.assertEqual([s["id"] for s in sources], [1, 2, 3])
        self.assertEqual(sources[0]["chapter"], "第7章 差旅")
        self.assertEqual(sources[1]["snippet"], "乙")

    def test_metadata_fallback(self):
        doc = SimpleNamespace(page_content="正文", metadata={})
        sources = number_sources([doc])
        self.assertEqual(sources[0]["chapter"], "未知章节")
        self.assertEqual(sources[0]["section"], "未知段落")

    def test_snippet_truncation(self):
        sources = number_sources([_doc("x" * 500)])
        self.assertEqual(len(sources[0]["snippet"]), 160)
        self.assertEqual(len(sources[0]["content"]), 500)  # 完整原文保留

    def test_format_compatible_with_legacy(self):
        """新格式 `来源 [1]: 章 > 节` 必须仍被旧解析方正确解析。"""
        sources = number_sources([_doc("住宿标准 500 元"), _doc("交通 100 元")])
        text = format_sources_text(sources)
        self.assertTrue(text.startswith("「知识库检索结果」"))
        self.assertIn("来源 [1]: 第7章 差旅 > 7.1 总则", text)
        self.assertIn("来源 [2]:", text)
        # 旧解析方 1：evaluate._pipeline_rank 按「来源 」切块还原排名
        blocks = text.split("来源 ")
        self.assertEqual(len(blocks), 3)  # 前缀 + 两个来源块
        self.assertIn("住宿标准 500 元", blocks[1])
        self.assertIn("交通 100 元", blocks[2])
        # 旧解析方 2：telemetry.extract_retrieved 的 startswith/split 口径
        for line in text.splitlines():
            if line.startswith("来源 "):
                parts = line.split(":", 1)
                self.assertEqual(len(parts), 2)
                self.assertIn(">", parts[1])


class ParseSourcesTest(unittest.TestCase):
    def test_roundtrip(self):
        """组装 → 解析互逆：编号/章节/小节/摘要还原。"""
        docs = [_doc("住宿标准 500 元/晚", "第2章 差旅", "2.2 住宿标准"),
                _doc("市内交通 100 元/天", "第2章 差旅", "2.3 交通")]
        text = format_sources_text(number_sources(docs))
        parsed = parse_sources_from_text(text)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["id"], 1)
        self.assertEqual(parsed[0]["chapter"], "第2章 差旅")
        self.assertEqual(parsed[0]["section"], "2.2 住宿标准")
        self.assertIn("500 元", parsed[0]["snippet"])
        self.assertEqual(parsed[1]["id"], 2)
        self.assertNotIn("content", parsed[0])  # 透出不带完整原文

    def test_non_policy_text_returns_empty(self):
        self.assertEqual(parse_sources_from_text(""), [])
        self.assertEqual(parse_sources_from_text(
            "知识库中未检索到相关政策，请提示用户询问 HR 人工。"), [])
        self.assertEqual(parse_sources_from_text("权限提示：您无权查询。"), [])


@unittest.skipUnless(_HAS_LANGCHAIN, "缺 langchain，跳过事件契约测试")
class SourcesEventContractTest(unittest.TestCase):
    """sources 事件契约：假图驱动 _stream_turn_impl，验证 additive 透出。"""

    class _FakeApp:
        """模拟一轮：tools 节点产出 search_hr_policy 的 ToolMessage。"""

        def __init__(self, with_policy: bool):
            self.with_policy = with_policy

        def stream(self, *_a, **_k):
            from langchain_core.messages import ToolMessage

            if self.with_policy:
                text = format_sources_text(number_sources([
                    _doc("住宿标准 500 元/晚", "第2章 差旅", "2.2 住宿标准")]))
                yield (ToolMessage(content=text, name="search_hr_policy",
                                   tool_call_id="c1"),
                       {"langgraph_node": "tools"})
            else:
                yield (ToolMessage(content="年假余额 6 天", name="get_leave_balance",
                                   tool_call_id="c2"),
                       {"langgraph_node": "tools"})

        def get_state(self, _config):
            return SimpleNamespace(next=(), values={})

    def _events(self, with_policy: bool) -> list[dict]:
        from agent.session_runner import _stream_turn_impl

        app = self._FakeApp(with_policy)
        return list(_stream_turn_impl(
            app, {"messages": []}, {"configurable": {"thread_id": "t-cite"}}))

    def test_sources_event_after_tool_result(self):
        events = self._events(with_policy=True)
        types = [e["type"] for e in events]
        self.assertIn("sources", types)
        # 紧随 tool_result 之后、done 之前
        self.assertLess(types.index("tool_result"), types.index("sources"))
        self.assertLess(types.index("sources"), types.index("done"))
        src = next(e for e in events if e["type"] == "sources")["sources"]
        self.assertEqual(src[0]["id"], 1)
        self.assertEqual(src[0]["chapter"], "第2章 差旅")
        self.assertIn("500 元", src[0]["snippet"])

    def test_non_policy_tool_no_sources_event(self):
        """非政策检索工具：不产出 sources 事件。"""
        events = self._events(with_policy=False)
        self.assertIn("tool_result", [e["type"] for e in events])
        self.assertNotIn("sources", [e["type"] for e in events])


if __name__ == "__main__":
    unittest.main()
