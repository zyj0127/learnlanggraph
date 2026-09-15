# -*- coding: utf-8 -*-
"""FastAPI 服务层：把 LangGraph HR 助理封装为 HTTP 接口。

接口：
- GET  /health         健康检查
- POST /chat/stream    SSE 流式问答：token 级推送；遇到敏感操作挂起时
                       推送 approval_required 事件，等待人工审批
- POST /chat/resume    人工审批后恢复执行（approve / reject），同样 SSE 流式返回

多轮会话状态由 LangGraph SqliteSaver 按 thread_id 持久化（服务重启不丢记忆），
单实例/轻量部署下无需额外引入 Redis；如需水平扩展可将 checkpointer 替换为
Redis/Postgres 实现，接口层无感知。

本地启动：
    uvicorn api.server:app --host 0.0.0.0 --port 8000
"""
import json
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from pydantic import BaseModel, Field

from agent.graph_builder import hr_agent_app
from logging_config import get_logger
from observability import UsageTracker
from telemetry import log_turn

logger = get_logger(__name__)

app = FastAPI(title="HR 智能助理 API", version="1.0.0")


class ChatRequest(BaseModel):
    uid: str = Field(description="当前登录员工 uid")
    question: str = Field(description="员工的自然语言问题")
    thread_id: str = Field(description="会话 ID，同一员工多轮对话保持一致")


class ResumeRequest(BaseModel):
    thread_id: str = Field(description="处于审批挂起状态的会话 ID")
    decision: str = Field(description="人工审批决定：approve 或 reject")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _event_stream(graph_input, config, meta: dict = None):
    """token 粒度推送 chatbot 节点输出；结束后检查是否挂起在人工审批。

    收尾处落一条会话埋点（延迟 / token / 是否转人工 / 是否被审计打回），
    让自助解决率这类业务指标有可核实的数据源；埋点失败不影响主链路。
    """
    tracker = UsageTracker()
    config = {**config, "callbacks": [tracker]}
    started = time.perf_counter()

    for msg, metadata in hr_agent_app.stream(graph_input, config, stream_mode="messages"):
        if metadata.get("langgraph_node") == "chatbot" and msg.content:
            yield _sse({"type": "token", "content": msg.content})

    state = hr_agent_app.get_state(config)
    if state.next:
        thread_id = config["configurable"]["thread_id"]
        logger.warning("会话挂起等待人工审批：thread=%s", thread_id)
        yield _sse({
            "type": "approval_required",
            "thread_id": thread_id,
            "detail": "检测到敏感操作（开具证明），请调用 /chat/resume 提交人工审批决定",
        })

    if meta:
        log_turn(
            session_id=config["configurable"]["thread_id"],
            question=meta.get("question", ""),
            uid=meta.get("uid", ""),
            channel=meta.get("channel", "http"),
            final_messages=(state.values or {}).get("messages", []),
            usage=tracker.summary(),
            latency_s=time.perf_counter() - started,
        )
    yield _sse({"type": "done"})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat/stream")
def chat_stream(req: ChatRequest):
    config = {"configurable": {"thread_id": req.thread_id}}

    # 首轮注入 uid 并初始化 loop_state（熔断计数）；后续轮仅追加消息，
    # 避免重置熔断计数器，同时由 checkpointer 恢复历史上下文
    existing = hr_agent_app.get_state(config).values or {}
    if existing.get("messages"):
        state = {"messages": [HumanMessage(content=req.question)]}
    else:
        state = {
            "messages": [HumanMessage(content=req.question)],
            "current_uid": req.uid,
            "loop_state": 0,
        }

    logger.info("流式问答请求：thread=%s uid=%s", req.thread_id, req.uid)
    return StreamingResponse(
        _event_stream(state, config, meta={
            "question": req.question, "uid": req.uid, "channel": "http",
        }),
        media_type="text/event-stream",
    )


@app.post("/chat/resume")
def chat_resume(req: ResumeRequest):
    if req.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision 必须是 approve 或 reject")

    config = {"configurable": {"thread_id": req.thread_id}}
    if not hr_agent_app.get_state(config).next:
        raise HTTPException(status_code=409, detail="该会话不存在或未处于审批挂起状态")

    logger.info("人工审批恢复：thread=%s decision=%s", req.thread_id, req.decision)
    return StreamingResponse(
        _event_stream(Command(resume=req.decision), config, meta={
            "question": f"[人工审批恢复] {req.decision}", "channel": "http",
        }),
        media_type="text/event-stream",
    )
