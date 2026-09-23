# -*- coding: utf-8 -*-
"""FastAPI 服务层冒烟测试：stub 掉 LangGraph 应用，不调用真实 LLM/数据库。

验证点：
1. /health 健康检查可用；
2. /chat/stream 以 SSE 格式输出 token 事件与 done 事件；
3. 敏感操作挂起时推送 approval_required 事件；
4. /chat/resume 参数校验与审批恢复流式输出。
"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class _StubGraph:
    """模拟 hr_agent_app：固定吐出两个 token，可按需模拟审批挂起。"""

    def __init__(self, interrupted=False):
        self.interrupted = interrupted
        self.stream_inputs = []

    def stream(self, graph_input, config, stream_mode="messages"):
        self.stream_inputs.append(graph_input)
        yield SimpleNamespace(content="你好"), {"langgraph_node": "chatbot"}
        yield SimpleNamespace(content="，员工"), {"langgraph_node": "chatbot"}
        # 非 chatbot 节点的输出不应推送
        yield SimpleNamespace(content="审计"), {"langgraph_node": "fact_checker"}
        # 审批恢复（Command 输入）执行完毕后，挂起状态随之清除
        from langgraph.types import Command
        if isinstance(graph_input, Command):
            self.interrupted = False

    def get_state(self, config):
        return SimpleNamespace(
            next=("human_review",) if self.interrupted else (),
            values={},
        )


def _parse_sse(body: str):
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


class ApiServerTest(unittest.TestCase):
    def _client(self, stub):
        from fastapi.testclient import TestClient
        import api.server as server

        patcher = patch.object(server, "hr_agent_app", stub)
        patcher.start()
        self.addCleanup(patcher.stop)
        return TestClient(server.app)

    def test_health(self):
        client = self._client(_StubGraph())
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_chat_stream_sse(self):
        stub = _StubGraph()
        client = self._client(stub)
        resp = client.post("/chat/stream", json={
            "uid": "1001", "question": "我还有几天年假？", "thread_id": "t1",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/event-stream", resp.headers["content-type"])

        events = _parse_sse(resp.text)
        types = [e["type"] for e in events]
        self.assertEqual(types, ["token", "token", "done"])
        full_text = "".join(e["content"] for e in events if e["type"] == "token")
        self.assertEqual(full_text, "你好，员工")

        # 首轮请求应注入 uid 与 loop_state
        first_input = stub.stream_inputs[0]
        self.assertEqual(first_input["current_uid"], "1001")
        self.assertEqual(first_input["loop_state"], 0)

    def test_approval_required_event(self):
        client = self._client(_StubGraph(interrupted=True))
        resp = client.post("/chat/stream", json={
            "uid": "1001", "question": "帮我开薪资证明", "thread_id": "t2",
        })
        events = _parse_sse(resp.text)
        types = [e["type"] for e in events]
        self.assertIn("approval_required", types)
        self.assertEqual(types[-1], "done")

    def test_resume_validation(self):
        client = self._client(_StubGraph())
        resp = client.post("/chat/resume", json={"thread_id": "t3", "decision": "maybe"})
        self.assertEqual(resp.status_code, 400)

    def test_resume_conflict_when_not_interrupted(self):
        client = self._client(_StubGraph(interrupted=False))
        resp = client.post("/chat/resume", json={"thread_id": "t4", "decision": "approve"})
        self.assertEqual(resp.status_code, 409)

    def test_resume_streams_after_approve(self):
        stub = _StubGraph(interrupted=True)
        client = self._client(stub)
        resp = client.post("/chat/resume", json={"thread_id": "t5", "decision": "approve"})
        self.assertEqual(resp.status_code, 200)
        events = _parse_sse(resp.text)
        self.assertEqual([e["type"] for e in events], ["token", "token", "done"])


if __name__ == "__main__":
    unittest.main()
