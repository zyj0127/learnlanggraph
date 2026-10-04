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
- {"type": "sources", "sources": [{id, chapter, section, snippet}, ...]}
      政策引用来源（additive 扩展：紧随 search_hr_policy 的 tool_result 之后
      透出；旧客户端忽略未知事件类型即可，契约向后兼容）
- {"type": "done", "usage": dict, "latency_s": float, "cache_hit": bool}
      本轮结束，附带用量统计与耗时；cache_hit=True 表示语义缓存命中
      （additive 字段，本轮 LLM 成本为零，usage 全 0）

埋点口径统一：无论 http 还是 streamlit 渠道，只要传入 meta 即调用 log_turn，
埋点失败不影响主链路。
"""
import time
from typing import Any, Dict, Iterator, Optional

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.constants import AUDIT_FALLBACK_MESSAGE, HANDOFF_PREFIX
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

    # ---- 语义缓存（任务⑥）：仅首轮/追问输入（dict）且带埋点问题时尝试 ----
    # 命中即按既有 token 事件流出（前端无感），本轮 LLM 成本为零；
    # 审批恢复（Command 输入）永不走缓存。缓存故障静默降级为正常执行。
    question = (meta or {}).get("question", "")
    if meta and isinstance(graph_input, dict) and question:
        from agent.semantic_cache import cache_lookup

        hit = cache_lookup(question)
        if hit is not None:
            try:
                # 与 chatbot 流式输出同口径：分片 token 事件
                answer = hit["answer"]
                chunk = 20
                for i in range(0, len(answer), chunk):
                    yield {"type": "token", "content": answer[i:i + chunk], "msg_id": None}
                if hit.get("sources"):
                    yield {"type": "sources", "sources": hit["sources"]}
                latency_s = time.perf_counter() - started
                usage = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0,
                         "total_tokens": 0, "estimated_cost_rmb": 0.0, "models": []}
                log_turn(
                    session_id=config["configurable"]["thread_id"],
                    question=question,
                    uid=meta.get("uid", ""),
                    channel=meta.get("channel", "http"),
                    final_messages=[AIMessage(content=answer)],
                    usage=usage, latency_s=latency_s,
                    cache_hit=True, model_tier="cache",
                )
                yield {"type": "done", "usage": usage, "latency_s": latency_s,
                       "cache_hit": True}
                return
            finally:
                if identity_token is not None:
                    from auth.context import reset_current_identity

                    reset_current_identity(identity_token)

    try:
        # ---- 本轮观测（缓存写入判定用）----
        tool_names_seen: list = []
        search_query_used: str = ""
        sources_seen: list = []
        approval_pending = False

        # ---- 流式幻觉预检（规则层前置）：只在句边界触发，半个数字不误判 ----
        # 命中即中止 token 流出，改发兜底话术（走既有 token 事件，前端无感）；
        # 事后审计（fact_check_node）保留不动，两层并存、同轮不双计。
        from agent.stream_guard import StreamFactGuard
        from config import get_settings

        guard = StreamFactGuard(
            question, enabled=get_settings().stream_fact_check_enabled)
        stream_blocked = False

        for msg, metadata in app.stream(graph_input, config, stream_mode="messages"):
            node = metadata.get("langgraph_node", "")

            # 工具调用阶段：透出给前端展示轨迹（HTTP 侧会忽略该事件类型）
            tool_calls = getattr(msg, "tool_calls", None)
            if tool_calls:
                for tool_call in tool_calls:
                    yield {"type": "tool_call", "tool_call": tool_call}
                    tool_names_seen.append(tool_call.get("name", ""))
                    if tool_call.get("name") == "search_hr_policy":
                        # 追问改写后的查询（缓存别名 key，改写后相同追问也能命中）
                        search_query_used = str(
                            (tool_call.get("args") or {}).get("query") or "")
                continue

            # 最终答案 token：仅 chatbot 节点的内容（SSE 契约保持不变）
            if node == "chatbot" and getattr(msg, "content", ""):
                if not stream_blocked:
                    violation = guard.feed(msg.content)
                    if violation is not None:
                        # 命中数字幻觉：立即停止本轮 token 流出，改发兜底话术
                        # （含事后审计打回后的重写流——本轮答案已判死刑，不再透出）
                        stream_blocked = True
                        from observability import AUDIT_COUNTERS

                        AUDIT_COUNTERS.record_block("rule", f"[流式预检] {violation}")
                        chunk = 20
                        for i in range(0, len(AUDIT_FALLBACK_MESSAGE), chunk):
                            yield {"type": "token",
                                   "content": AUDIT_FALLBACK_MESSAGE[i:i + chunk],
                                   "msg_id": None}
                if not stream_blocked:
                    yield {"type": "token", "content": msg.content,
                           "msg_id": getattr(msg, "id", None)}
            elif isinstance(msg, ToolMessage):
                yield {"type": "tool_result", "name": msg.name or "tool", "content": str(msg.content)}
                # 引用溯源（additive）：政策检索结果随 tool_result 立即解析透出
                # sources 事件（文本即事实源，不依赖 contextvars 跨 langchain
                # 调用边界传播）；旧客户端忽略未知事件类型即可
                if (msg.name or "") == "search_hr_policy":
                    from agent.citations import parse_sources_from_text

                    sources = parse_sources_from_text(str(msg.content))
                    if sources:
                        sources_seen = sources
                        yield {"type": "sources", "sources": sources}
                    # 流式预检的比对原文（本轮 RAG 上下文）
                    guard.set_context(str(msg.content))

        # 流结束终检：句边界后的残余缓冲（此时答案完整，无切半风险）
        if not stream_blocked:
            violation = guard.flush()
            if violation is not None:
                stream_blocked = True
                from observability import AUDIT_COUNTERS

                AUDIT_COUNTERS.record_block("rule", f"[流式预检-终检] {violation}")
                chunk = 20
                for i in range(0, len(AUDIT_FALLBACK_MESSAGE), chunk):
                    yield {"type": "token",
                           "content": AUDIT_FALLBACK_MESSAGE[i:i + chunk],
                           "msg_id": None}

        # 兜底：messages 流不直接透出 interrupt，改查图状态
        state = app.get_state(config)
        if state.next:
            thread_id = config["configurable"]["thread_id"]
            logger.warning("会话挂起等待人工审批：thread=%s", thread_id)
            approval_pending = True
            yield {
                "type": "approval_required",
                "thread_id": thread_id,
                "detail": APPROVAL_REQUIRED_DETAIL,
                "interrupt_value": _extract_interrupt_value(state),
            }

        latency_s = time.perf_counter() - started
        usage = tracker.summary()

        final_messages = (state.values or {}).get("messages", [])
        if meta:
            from agent.model_router import usage_tier

            log_turn(
                session_id=config["configurable"]["thread_id"],
                question=meta.get("question", ""),
                uid=meta.get("uid", ""),
                channel=meta.get("channel", "http"),
                final_messages=final_messages,
                usage=usage,
                latency_s=latency_s,
                model_tier=usage_tier(usage.get("models") or []),
            )

        # ---- 语义缓存写入判定（任务⑥）----
        # 只缓存「纯政策问答」：本轮工具调用 ⊆ {search_hr_policy}（政策是公共
        # 信息，与 uid 无关）、无审批挂起、最终答案非转人工/熔断话术。
        # 含个人数据（档案/余额）或写操作（证明/请假）的答案绝不缓存；
        # 审计打回后重写的最终答案本身就是审计通过的产物，可以缓存。
        if meta and question and not approval_pending and not stream_blocked:
            from agent.semantic_cache import cache_store

            last_msg = final_messages[-1] if final_messages else None
            answer = getattr(last_msg, "content", "") or ""
            if (answer
                    and isinstance(last_msg, AIMessage)
                    and tool_names_seen
                    and set(tool_names_seen) <= {"search_hr_policy"}
                    and not answer.startswith(HANDOFF_PREFIX)
                    and not answer.startswith(AUDIT_FALLBACK_MESSAGE)):
                cache_store(question, answer, sources=sources_seen or None,
                            alias=search_query_used or None)
        yield {"type": "done", "usage": usage, "latency_s": latency_s,
               "cache_hit": False}
    finally:
        if identity_token is not None:
            from auth.context import reset_current_identity

            reset_current_identity(identity_token)
