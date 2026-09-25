# -*- coding: utf-8 -*-
"""公共会话执行层：FastAPI 服务与 Streamlit 前端共用的 Graph 驱动逻辑。

为什么抽这一层
--------------
api/server.py 与 streamlit_app.py 原先各自实现了几乎相同的「流式消费 +
首轮状态构造 + 审批恢复 + 挂起检测」逻辑，且埋点口径不一致（server 有
log_turn，streamlit 没有）。本模块把它收敛为唯一实现：

- build_turn_state()  首轮/后续轮输入状态构造（首轮注入 uid 并初始化熔断计数）
- stream_turn()       驱动 Graph 流式执行，产出统一事件流，并在收尾处落埋点

事件协议（dict，"type" 字段区分）：
- {"type": "token", "content": str, "msg_id": str|None}
      chatbot 节点的最终答案 token（SSE 与前端渲染共用）
- {"type": "tool_call", "tool_call": dict}
      AI 发起工具调用（前端展示执行轨迹用；HTTP 侧不透出，保持 SSE 契约不变）
- {"type": "tool_result", "name": str, "content": str}
      工具返回结果（同上）
- {"type": "approval_required", "thread_id": str, "detail": str, "interrupt_value": Any}
      Graph 挂起在人工审批节点
- {"type": "done", "usage": dict, "latency_s": float}
      本轮结束，附带用量统计与耗时

埋点口径统一：无论 http 还是 streamlit 渠道，只要传入 meta 即调用 log_turn，
埋点失败不影响主链路。
"""
import time
from typing import Any, Dict, Iterator, Optional

from langchain_core.messages import HumanMessage, ToolMessage

from logging_config import get_logger
from observability import UsageTracker
from telemetry import log_turn

logger = get_logger(__name__)

# 审批挂起时对外统一的话术（HTTP 与前端共用，避免两端文案漂移）
APPROVAL_REQUIRED_DETAIL = "检测到敏感操作（开具证明），请提交人工审批决定（approve / reject）"


def build_turn_state(app, config: dict, uid: str, question: str) -> Dict[str, Any]:
    """构造一轮问答的 Graph 输入状态。

    首轮注入 uid 并初始化 loop_state（熔断计数）；后续轮仅追加消息，
    避免重置熔断计数器，同时由 checkpointer 恢复历史上下文。
    """
    existing = app.get_state(config).values or {}
    if existing.get("messages"):
        return {"messages": [HumanMessage(content=question)]}
    return {
        "messages": [HumanMessage(content=question)],
        "current_uid": uid,
        "loop_state": 0,
    }


def _extract_interrupt_value(state) -> Any:
    """从图状态快照中取 interrupt 负载（审批提示文案），无则 None。"""
    for task in getattr(state, "tasks", None) or []:
        if getattr(task, "interrupts", None):
            return task.interrupts[0].value
    return None


def stream_turn(app, graph_input, config: dict,
                meta: Optional[Dict[str, Any]] = None,
                identity=None) -> Iterator[Dict[str, Any]]:
    """驱动 Graph 执行一轮（首轮输入或审批恢复 Command），产出统一事件流。

    graph_input: 首轮为 state dict（见 build_turn_state），审批恢复为
    Command(resume=...)。meta 提供埋点字段：question / uid / channel，
    为 None 时跳过埋点。
    identity: auth.models.Identity，非 None 时在本轮执行期间设置请求身份
    上下文（contextvars），供工具层 RBAC 校验；None 表示不设置
    （AUTH_ENABLED=false 旁路或调用方未认证，工具层按匿名身份处理）。
    审批恢复路径同样由本参数恢复身份上下文（审批人与申请人身份分离）。

    OpenTelemetry（可选）：配置了 OTEL_EXPORTER_OTLP_ENDPOINT 时整轮包一个
    "stream_turn" span（channel / uid_hash 脱敏 / thread_id / latency / usage）；
    未配置或 otel 未安装时 get_tracer() 返回 None，行为与之前完全一致。
    """
    from observability.otel import get_tracer, hash_uid

    tracer = get_tracer()
    if tracer is None:
        yield from _stream_turn_impl(app, graph_input, config, meta=meta, identity=identity)
        return

    attributes = {
        "channel": (meta or {}).get("channel", "http"),
        "uid_hash": hash_uid((meta or {}).get("uid", "")),
        "thread_id": config.get("configurable", {}).get("thread_id", ""),
    }
    with tracer.start_as_current_span("stream_turn", attributes=attributes) as span:
        for event in _stream_turn_impl(app, graph_input, config, meta=meta, identity=identity):
            if event["type"] == "done":
                span.set_attribute("latency_s", round(event.get("latency_s", 0.0), 4))
                usage = event.get("usage") or {}
                for key in ("total_tokens", "input_tokens", "output_tokens"):
                    if isinstance(usage.get(key), (int, float)):
                        span.set_attribute(f"usage.{key}", int(usage[key]))
            yield event


def _stream_turn_impl(app, graph_input, config: dict,
                      meta: Optional[Dict[str, Any]] = None,
                      identity=None) -> Iterator[Dict[str, Any]]:
    """stream_turn 的实际执行体（事件协议见 stream_turn docstring）。"""
    tracker = UsageTracker()
    callbacks = [tracker]
    # Langfuse 可观测性（未配置时返回 None 静默降级，与既有埋点并存）
    from observability.langfuse import get_langfuse_handler

    langfuse_handler = get_langfuse_handler()
    if langfuse_handler is not None:
        callbacks.append(langfuse_handler)
    config = {**config, "callbacks": callbacks}
    started = time.perf_counter()

    # 身份上下文贯穿本轮执行（含审批恢复路径）；finally 复位，防泄漏到下一请求
    identity_token = None
    if identity is not None:
        from auth.context import set_current_identity

        identity_token = set_current_identity(identity)

    try:
        for msg, metadata in app.stream(graph_input, config, stream_mode="messages"):
            node = metadata.get("langgraph_node", "")

            # 工具调用阶段：透出给前端展示轨迹（HTTP 侧会忽略该事件类型）
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                for tool_call in tool_calls:
                    yield {"type": "tool_call", "tool_call": tool_call}
                continue

            # 最终答案 token：仅 chatbot 节点的内容（SSE 契约保持不变）
            if node == "chatbot" and getattr(msg, "content", ""):
                yield {"type": "token", "content": msg.content, "msg_id": getattr(msg, "id", None)}
            elif isinstance(msg, ToolMessage):
                yield {"type": "tool_result", "name": msg.name or "tool", "content": str(msg.content)}

        # 兜底：messages 流不直接透出 interrupt，改查图状态
        state = app.get_state(config)
        if state.next:
            thread_id = config["configurable"]["thread_id"]
            logger.warning("会话挂起等待人工审批：thread=%s", thread_id)
            yield {
                "type": "approval_required",
                "thread_id": thread_id,
                "detail": APPROVAL_REQUIRED_DETAIL,
                "interrupt_value": _extract_interrupt_value(state),
            }

        latency_s = time.perf_counter() - started
        usage = tracker.summary()

        if meta:
            log_turn(
                session_id=config["configurable"]["thread_id"],
                question=meta.get("question", ""),
                uid=meta.get("uid", ""),
                channel=meta.get("channel", "http"),
                final_messages=(state.values or {}).get("messages", []),
                usage=usage,
                latency_s=latency_s,
            )
        yield {"type": "done", "usage": usage, "latency_s": latency_s}
    finally:
        if identity_token is not None:
            from auth.context import reset_current_identity

            reset_current_identity(identity_token)
