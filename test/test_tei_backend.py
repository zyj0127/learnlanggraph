# -*- coding: utf-8 -*-
"""TEI 推理后端单测：reranker HTTP 封装 + 工厂开关 + local 默认回归。

运行：python -m unittest test.test_tei_backend -v

零外部依赖设计：
- TEIReranker 的 HTTP 层 mock 掉（patch agent.tei._post_json），不起真实服务；
- 工厂测试在缺少 langchain_openai 的环境下自动跳过（对齐 test_auth_rbac 模式）；
- 所有用例结束前恢复 lru_cache 与原工厂，避免污染其他测试。
"""
import unittest
from types import SimpleNamespace
from unittest import mock

from agent.tei import TEIReranker, normalize_tei_base


class NormalizeUrlTest(unittest.TestCase):
    def test_strip_trailing_slash(self):
        self.assertEqual(normalize_tei_base("http://tei:80/"), "http://tei:80")
        self.assertEqual(normalize_tei_base(" http://tei:80 "), "http://tei:80")
        self.assertEqual(normalize_tei_base(""), "")


class TEIRerankerTest(unittest.TestCase):
    def test_empty_url_raises(self):
        with self.assertRaises(ValueError):
            TEIReranker("")

    def test_predict_maps_scores_back_by_index(self):
        """TEI 响应按 score 降序返回，必须按 index 映射回原输入顺序。"""
        reranker = TEIReranker("http://tei-reranker:80/")
        fake_response = [
            {"index": 1, "score": 0.95},
            {"index": 0, "score": 0.12},
        ]
        with mock.patch("agent.tei._post_json", return_value=fake_response) as post:
            scores = reranker.predict([["年假几天？", "文档A"], ["年假几天？", "文档B"]])
        self.assertEqual(scores, [0.12, 0.95])
        # 同 query 多 doc 合并为一次 /rerank 请求
        self.assertEqual(post.call_count, 1)
        args, kwargs = post.call_args
        self.assertEqual(args[0], "http://tei-reranker:80/rerank")
        self.assertEqual(args[1]["texts"], ["文档A", "文档B"])

    def test_predict_groups_by_query(self):
        """不同 query 分别发请求，结果仍与输入顺序一一对应。"""
        reranker = TEIReranker("http://tei-reranker:80")

        def fake_post(url, payload, timeout=30.0):
            return [{"index": i, "score": float(i)} for i in range(len(payload["texts"]))]

        with mock.patch("agent.tei._post_json", side_effect=fake_post) as post:
            scores = reranker.predict([
                ["q1", "d1"], ["q2", "d2"], ["q1", "d3"],
            ])
        self.assertEqual(post.call_count, 2)
        # q1 组：d1→0.0, d3→1.0；q2 组：d2→0.0
        self.assertEqual(scores, [0.0, 0.0, 1.0])

    def test_predict_empty_input(self):
        reranker = TEIReranker("http://tei-reranker:80")
        self.assertEqual(reranker.predict([]), [])

    def test_predict_missing_index_raises(self):
        reranker = TEIReranker("http://tei-reranker:80")
        with mock.patch("agent.tei._post_json", return_value=[{"index": 0, "score": 0.5}]):
            with self.assertRaises(ValueError):
                reranker.predict([["q", "d1"], ["q", "d2"]])


try:
    import langchain_openai  # noqa: F401

    HAS_LANGCHAIN_OPENAI = True
except ModuleNotFoundError:
    HAS_LANGCHAIN_OPENAI = False


@unittest.skipUnless(HAS_LANGCHAIN_OPENAI, "缺少 langchain_openai，跳过工厂开关测试")
class BackendSwitchTest(unittest.TestCase):
    """get_embeddings / get_reranker 的 local|tei 开关（模型/HTTP 均 mock）。"""

    def setUp(self):
        import agent.rag_pipeline as rp

        self.rp = rp
        rp.get_embeddings.cache_clear()
        rp.get_reranker.cache_clear()
        self.addCleanup(rp.get_embeddings.cache_clear)
        self.addCleanup(rp.get_reranker.cache_clear)

    def _fake_settings(self, backend, emb_url="", rerank_url=""):
        fake = SimpleNamespace(
            embedding_backend=backend,
            tei_embedding_url=emb_url,
            tei_reranker_url=rerank_url,
        )
        patcher = mock.patch.object(self.rp, "get_settings", lambda: fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_tei_embeddings_uses_openai_compatible_client(self):
        self._fake_settings("tei", emb_url="http://tei-embedding:80/")
        with mock.patch("langchain_openai.OpenAIEmbeddings") as cls:
            self.rp.get_embeddings()
        kwargs = cls.call_args.kwargs
        self.assertEqual(kwargs["base_url"], "http://tei-embedding:80/v1")
        self.assertFalse(kwargs["check_embedding_ctx_length"])

    def test_tei_embeddings_requires_url(self):
        self._fake_settings("tei", emb_url="")
        with self.assertRaises(RuntimeError):
            self.rp.get_embeddings()

    def test_tei_reranker_returns_tei_client(self):
        self._fake_settings("tei", rerank_url="http://tei-reranker:80")
        reranker = self.rp.get_reranker()
        self.assertIsInstance(reranker, TEIReranker)

    def test_local_default_unchanged(self):
        """默认 local 路径仍走 HuggingFaceEmbeddings / CrossEncoder（行为回归）。"""
        self._fake_settings("local")
        import sys
        fake_hf = mock.MagicMock()
        fake_st = mock.MagicMock()
        with mock.patch.dict(sys.modules, {
            "langchain_huggingface": fake_hf,
            "sentence_transformers": fake_st,
        }):
            self.rp.get_embeddings()
            self.rp.get_reranker()
        self.assertEqual(fake_hf.HuggingFaceEmbeddings.call_count, 1)
        self.assertEqual(fake_st.CrossEncoder.call_count, 1)


if __name__ == "__main__":
    unittest.main()
