"""OpenTelemetry 静默降级 + Prometheus /metrics 端点单测。

运行：python -m unittest test.test_observability_otel -v

零外部依赖设计：
- otel 未配置 endpoint 的降级路径不依赖 opentelemetry 包；
- stream_turn 降级回归用 stub graph，meta=None（不写埋点库）；
- /metrics 测试在缺少 fastapi / prometheus_client 时自动跳过。
"""
import unittest
from types import SimpleNamespace
from unittest import mock

from observability import otel


class OTelDegradeTest(unittest.TestCase):
    def setUp(self):
        otel.get_tracer.cache_clear()
        self.addCleanup(otel.get_tracer.cache_clear)

    def _patch_settings(self, endpoint=""):
        fake = SimpleNamespace(
            otel_exporter_otlp_endpoint=endpoint,
            otel_service_name="hr-agent-test",
        )
        patcher = mock.patch.object(otel, "get_settings", lambda: fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_endpoint_returns_none(self):
        """OTEL_EXPORTER_OTLP_ENDPOINT 未配置：get_tracer() 返回 None。"""
        self._patch_settings("")
        self.assertIsNone(otel.get_tracer())

    def test_init_failure_degrades(self):
        """endpoint 已配置但 opentelemetry 包不可用：静默降级返回 None。"""
        self._patch_settings("http://otel-collector:4318")
        import sys
        # 屏蔽 opentelemetry 相关模块模拟「包未安装」
        blocked = {name: None for name in list(sys.modules) if name.startswith("opentelemetry")}
        with mock.patch.dict(sys.modules, blocked):
            self.assertIsNone(otel.get_tracer())

    def test_hash_uid_masks_plaintext(self):
        h = otel.hash_uid("1001")
        self.assertEqual(len(h), 12)
        self.assertNotIn("1001", h)
        self.assertEqual(otel.hash_uid(""), "")

    def test_stream_turn_silent_degrade(self):
        """未配置 otel 时 stream_turn 全流程行为不变（token/done 事件照常）。"""
        self._patch_settings("")
        from agent.session_runner import stream_turn

        stub = SimpleNamespace(
            stream=lambda *a, **k: iter([
                (SimpleNamespace(content="你好"), {"langgraph_node": "chatbot"}),
            ]),
            get_state=lambda config: SimpleNamespace(next=(), values={"messages": []}),
        )
        events = list(stream_turn(
            stub, {"messages": []}, {"configurable": {"thread_id": "t-otel"}},
            meta=None,
        ))
        self.assertEqual([e["type"] for e in events], ["token", "done"])


try:
    import fastapi  # noqa: F401
    import prometheus_client  # noqa: F401

    HAS_METRICS_DEPS = True
except ModuleNotFoundError:
    HAS_METRICS_DEPS = False


@unittest.skipUnless(HAS_METRICS_DEPS, "缺少 fastapi/prometheus_client，跳过 /metrics 测试")
class MetricsEndpointTest(unittest.TestCase):
    """FastAPI /metrics：请求计数 + 延迟直方图 + RBAC 拦截计数镜像。"""

    def _client(self):
        from fastapi.testclient import TestClient

        import api.server as server

        stub = SimpleNamespace(
            stream=lambda *a, **k: iter([]),
            get_state=lambda config: SimpleNamespace(next=(), values={}),
        )
        patcher = mock.patch.object(server, "hr_agent_app", stub)
        patcher.start()
        self.addCleanup(patcher.stop)
        return TestClient(server.app)

    def test_metrics_endpoint_exposes_request_metrics(self):
        client = self._client()
        client.get("/health")
        resp = client.get("/metrics")
        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("hr_agent_http_requests_total", body)
        self.assertIn("hr_agent_http_request_latency_seconds", body)
        self.assertIn('endpoint="/health"', body)

    def test_metrics_mirrors_rbac_denied_counter(self):
        """audit.record_access_denied 同时写 Prometheus（不破坏 audit 原口径）。"""
        from observability.audit import AUDIT_COUNTERS

        AUDIT_COUNTERS.reset()
        self.addCleanup(AUDIT_COUNTERS.reset)

        client = self._client()
        AUDIT_COUNTERS.record_access_denied("view_profile")
        resp = client.get("/metrics")
        self.assertEqual(resp.status_code, 200)
        self.assertIn('hr_agent_rbac_denied_total{action="view_profile"}', resp.text)
        # audit 原口径不变
        self.assertEqual(AUDIT_COUNTERS.snapshot()["access_denied"], 1)


if __name__ == "__main__":
    unittest.main()
