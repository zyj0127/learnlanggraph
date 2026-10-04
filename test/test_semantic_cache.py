# -*- coding: utf-8 -*-
"""语义缓存测试：命中/未命中/TTL/别名/开关/统计 + session_runner 命中短路契约。

全部用假 embedding 与 tmp 库，零外部依赖（不加载 BGE、不联网）。
"""
import sqlite3
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from agent import semantic_cache as sc

# 假 embedding：含「差旅」→ [1,0]，其余 → [0,1]（同主题相似度 1.0，跨主题 0.0）
def _fake_embed(text: str):
    return [1.0, 0.0] if "差旅" in text else [0.0, 1.0]


def _settings(db, enabled=True, threshold=0.92, ttl=7):
    return SimpleNamespace(
        semantic_cache_enabled=enabled,
        semantic_cache_threshold=threshold,
        semantic_cache_ttl_days=ttl,
        semantic_cache_db=db,
    )


class _CacheCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.db = Path(self._tmp.name) / "cache.db"
        self.settings = _settings(self.db)
        self._p = patch.object(sc, "get_settings", return_value=self.settings)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()


class TestStoreAndLookup(_CacheCase):
    def test_hit_returns_answer_and_sources(self):
        ok = sc.cache_store("差旅住宿标准是多少？", "一线 500 元/晚[1]。",
                            sources=[{"id": 1, "chapter": "2", "section": "2.2",
                                      "snippet": "..."}],
                            embed_fn=_fake_embed, db_path=str(self.db))
        self.assertTrue(ok)
        hit = sc.cache_lookup("差旅住宿怎么报销？", embed_fn=_fake_embed,
                              db_path=str(self.db))
        self.assertIsNotNone(hit)
        self.assertEqual(hit["answer"], "一线 500 元/晚[1]。")
        self.assertEqual(hit["sources"][0]["section"], "2.2")
        self.assertEqual(hit["similarity"], 1.0)

    def test_miss_on_different_topic(self):
        sc.cache_store("差旅住宿标准是多少？", "一线 500 元/晚。",
                       embed_fn=_fake_embed, db_path=str(self.db))
        hit = sc.cache_lookup("病假能休几天？", embed_fn=_fake_embed,
                              db_path=str(self.db))
        self.assertIsNone(hit)  # 相似度 0.0 < 0.92

    def test_hit_count_and_counters(self):
        sc.cache_store("差旅标准？", "500 元。", embed_fn=_fake_embed,
                       db_path=str(self.db))
        sc.cache_lookup("差旅报销？", embed_fn=_fake_embed, db_path=str(self.db))
        sc.cache_lookup("病假呢？", embed_fn=_fake_embed, db_path=str(self.db))
        stats = sc.cache_stats(db_path=str(self.db))
        self.assertEqual(stats["lookups"], 2)
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["hit_rate"], 0.5)
        self.assertEqual(stats["entries"], 1)
        conn = sqlite3.connect(str(self.db))
        try:
            hits = conn.execute("SELECT hits FROM cache_entries").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(hits, 1)

    def test_alias_store_rewrite_query_hits(self):
        # 追问改写别名：原问与改写后查询各写一行，改写后的相同追问也能命中
        sc.cache_store("那病假呢？", "病假每年 10 天[1]。", alias="病假的相关规定是什么",
                       embed_fn=_fake_embed, db_path=str(self.db))
        hit = sc.cache_lookup("病假相关规定", embed_fn=_fake_embed,
                              db_path=str(self.db))
        self.assertIsNotNone(hit)
        stats = sc.cache_stats(db_path=str(self.db))
        self.assertEqual(stats["entries"], 2)  # 原问 + 别名两行

    def test_threshold_configurable(self):
        # 阈值调到 1.0 以上 → 永远未命中（阈值不可达时行为与无缓存一致）
        strict = _settings(self.db, threshold=1.01)
        with patch.object(sc, "get_settings", return_value=strict):
            sc.cache_store("差旅标准？", "500 元。", embed_fn=_fake_embed,
                           db_path=str(self.db))
            self.assertIsNone(sc.cache_lookup("差旅标准？", embed_fn=_fake_embed,
                                              db_path=str(self.db)))


class TestTTLAndSwitch(_CacheCase):
    def test_expired_entries_purged(self):
        sc.cache_store("差旅标准？", "500 元。", embed_fn=_fake_embed,
                       db_path=str(self.db))
        old = (datetime.now() - timedelta(days=8)).isoformat(timespec="seconds")
        conn = sqlite3.connect(str(self.db))
        try:
            conn.execute("UPDATE cache_entries SET created_at = ?", (old,))
            conn.commit()
        finally:
            conn.close()
        self.assertIsNone(sc.cache_lookup("差旅标准？", embed_fn=_fake_embed,
                                          db_path=str(self.db)))
        self.assertEqual(sc.cache_stats(db_path=str(self.db))["entries"], 0)

    def test_disabled_bypasses_everything(self):
        off = _settings(self.db, enabled=False)
        with patch.object(sc, "get_settings", return_value=off):
            self.assertFalse(sc.cache_store("差旅标准？", "500 元。",
                                            embed_fn=_fake_embed, db_path=str(self.db)))
            self.assertIsNone(sc.cache_lookup("差旅标准？", embed_fn=_fake_embed,
                                              db_path=str(self.db)))
        # 关闭期间未写入任何条目
        self.assertEqual(sc.cache_stats(db_path=str(self.db))["entries"], 0)

    def test_empty_inputs_guarded(self):
        self.assertFalse(sc.cache_store("", "答案", embed_fn=_fake_embed,
                                        db_path=str(self.db)))
        self.assertFalse(sc.cache_store("问题", "", embed_fn=_fake_embed,
                                        db_path=str(self.db)))
        self.assertIsNone(sc.cache_lookup("", embed_fn=_fake_embed,
                                          db_path=str(self.db)))


class TestCosine(unittest.TestCase):
    def test_pure_math(self):
        self.assertAlmostEqual(sc.cosine_similarity([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(sc.cosine_similarity([1, 0], [0, 1]), 0.0)
        self.assertEqual(sc.cosine_similarity([0, 0], [1, 1]), 0.0)


class TestRunnerCacheHitContract(unittest.TestCase):
    """session_runner 命中短路：图不被驱动，事件序列 token...→sources→done(cache_hit)。"""

    def test_hit_short_circuits_graph(self):
        from agent import session_runner

        class _AppShouldNotRun:
            def stream(self, *a, **k):
                raise AssertionError("缓存命中时图不应被驱动")

        answer = "一线城市住宿 500 元/晚[1]。"
        hit = {"answer": answer,
               "sources": [{"id": 1, "chapter": "2", "section": "2.2", "snippet": "s"}],
               "similarity": 0.99}
        config = {"configurable": {"thread_id": "t-cache"}}
        meta = {"question": "差旅住宿标准？", "uid": "1001", "channel": "http"}
        with patch("agent.semantic_cache.cache_lookup", return_value=hit), \
                patch("agent.session_runner.log_turn", return_value=1) as log_mock:
            events = list(session_runner._stream_turn_impl(
                _AppShouldNotRun(), {"messages": []}, config, meta=meta))

        types = [e["type"] for e in events]
        self.assertEqual(types[-1], "done")
        self.assertTrue(events[-1]["cache_hit"])
        self.assertEqual(events[-1]["usage"]["total_tokens"], 0)  # 命中零成本
        self.assertIn("sources", types)
        tokens = "".join(e["content"] for e in events if e["type"] == "token")
        self.assertEqual(tokens, answer)
        # 埋点记 cache_hit=True / model_tier=cache
        _, kwargs = log_mock.call_args
        self.assertTrue(kwargs["cache_hit"])
        self.assertEqual(kwargs["model_tier"], "cache")

    def test_miss_falls_through_to_graph(self):
        from langchain_core.messages import AIMessage

        from agent import session_runner

        class _FakeApp:
            def stream(self, graph_input, config, stream_mode=None):
                yield (AIMessage(content="答案"),
                       {"langgraph_node": "chatbot"})

            def get_state(self, config):
                return SimpleNamespace(
                    next=None,
                    values={"messages": [AIMessage(content="答案")]})

        config = {"configurable": {"thread_id": "t-miss"}}
        meta = {"question": "差旅住宿标准？", "uid": "1001", "channel": "http"}
        with patch("agent.semantic_cache.cache_lookup", return_value=None), \
                patch("agent.session_runner.log_turn", return_value=1), \
                patch("agent.semantic_cache.cache_store", return_value=True) as store_mock:
            events = list(session_runner._stream_turn_impl(
                _FakeApp(), {"messages": []}, config, meta=meta))
        self.assertFalse(events[-1]["cache_hit"])
        # 无工具调用（tool_names_seen 空）→ 不写入缓存
        store_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
