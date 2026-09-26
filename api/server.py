# -*- coding: utf-8 -*-
"""FastAPI 服务层：把 LangGraph HR 助理封装为 HTTP 接口。

接口：
- GET  /health         健康检查
- POST /auth/token     开发用 token 签发（uid+role 换 JWT；仅 AUTH_DEV_MODE=true 时启用，
                       生产关闭，token 由企业 SSO 签发——见 auth/jwt_tokens.py 扩展点）
- POST /chat/stream    SSE 流式问答：token 级推送；遇到敏感操作挂起时
                       推送 approval_required 事件，等待人工审批
- POST /chat/resume    人工审批后恢复执行（approve / reject），同样 SSE 流式返回；
                       审批人必须是 HR/ADMIN（防止员工自审自批）

认证（企业化第二阶段）：
- 请求头携带 `Authorization: Bearer <JWT>`（HS256，密钥走 Settings.jwt_secret；
  RS256/OIDC JWKS 扩展点见 auth/jwt_tokens.py 模块注释）。
- token payload → Identity → contextvars，贯穿整个 SSE 流式请求；
  无 token 时按匿名身份处理（政策问答可用，涉及个人数据的工具被 RBAC 拦下）。
- Settings.auth_enabled=false 时所有鉴权逻辑完全旁路，回到旧行为。
- SSE 事件格式与既有契约不变（token / approval_required / done）。

多轮会话状态由 LangGraph checkpointer 按 thread_id 持久化（服务重启不丢记忆）。
Graph 驱动与埋点统一走 agent/session_runner.py（与 Streamlit 前端共用），
本文件只负责 HTTP 协议适配（认证、SSE 序列化与参数校验）。

本地启动：
    uvicorn api.server:app --host 0.0.0.0 --port 8000
"""
import json
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

from agent.graph_builder import hr_agent_app
from agent.session_runner import build_turn_state, stream_turn
from auth.guard import audit_approval, check_approval_allowed, extract_applicant_uid
from auth.jwt_tokens import decode_token, encode_token
from auth.models import Identity, Role
from config import get_settings
from logging_config import get_logger

logger = get_logger(__name__)

app = FastAPI(title="HR 智能助理 API", version="2.0.0")


class ChatRequest(BaseModel):
    uid: str = Field(description="当前登录员工 uid")
    question: str = Field(description="员工的自然语言问题")
    thread_id: str = Field(description="会话 ID，同一员工多轮对话保持一致")


class ResumeRequest(BaseModel):
    thread_id: str = Field(description="处于审批挂起状态的会话 ID")
    decision: str = Field(description="人工审批决定：approve 或 reject")


class TokenRequest(BaseModel):
    uid: str = Field(description="员工 uid")
    role: str = Field(description="角色：employee / hr / admin")
    name: str = Field(default="", description="姓名（可选）")
    department: str = Field(default="", description="部门（可选）")


def get_request_identity(request: Request) -> Optional[Identity]:
    """认证依赖：Authorization: Bearer <JWT> → Identity。

    - AUTH_ENABLED=false：返回 None，完全旁路（旧行为）。
    - 无 Authorization 头：返回匿名身份（只读政策问答可用，个人数据工具被 RBAC 拦下）。
    - token 无效/过期：401。
    """
    settings = get_settings()
    if not settings.auth_enabled:
        return None

    authz = request.headers.get("Authorization", "")
    if not authz:
        return Identity(role=Role.ANONYMOUS)  # 无 token：匿名身份（最低权限）
    if not authz.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization 头必须是 Bearer <token>")

    if not settings.jwt_secret:
        # 密钥未配置时不放行任何 token（安全优先，避免空密钥验签）
        logger.error("JWT_SECRET 未配置，无法校验 token")
        raise HTTPException(status_code=500, detail="服务端认证未配置（JWT_SECRET 缺失）")

    identity = decode_token(
        authz[len("Bearer "):].strip(),
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    if identity is None:
        raise HTTPException(status_code=401, detail="token 无效或已过期")
    return identity


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _event_stream(graph_input, config: dict, meta: dict = None, identity=None):
    """把 session_runner 的统一事件流翻译为 SSE 帧。

    SSE 契约（保持不变）：只透出 token / approval_required / done 三类事件，
    tool_call / tool_result 等前端展示事件在 HTTP 侧不透出。
    identity 由 session_runner 设置进 contextvars，贯穿整轮执行（含审批恢复）。
    """
    for event in stream_turn(hr_agent_app, graph_input, config, meta=meta, identity=identity):
        event_type = event["type"]
        if event_type == "token":
            yield _sse({"type": "token", "content": event["content"]})
        elif event_type == "approval_required":
            # detail 优先取 interrupt 负载中的文案（请假申请含类型/日期/天数/事由详情）；
            # 兼容旧纯字符串负载与缺失场景，回退固定文案，SSE 契约不变
            interrupt_value = event.get("interrupt_value")
            detail = None
            if isinstance(interrupt_value, dict):
                detail = interrupt_value.get("message")
            elif isinstance(interrupt_value, str):
                detail = interrupt_value
            yield _sse({
                "type": "approval_required",
                "thread_id": event["thread_id"],
                "detail": detail or "检测到敏感操作（开具证明），请调用 /chat/resume 提交人工审批决定",
            })
        elif event_type == "done":
            yield _sse({"type": "done"})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    """Prometheus 抓取端点（企业化第二阶段监控）。

    prometheus_client 未安装时返回 501（依赖已钉版，正常部署不会走到）。
    端点本身无鉴权：指标不含 PII（uid 不落标签，RBAC 仅 action 枚举值），
    生产环境如需收敛可在 nginx/网关层限制来源。
    """
    from observability import prom

    rendered = prom.render_metrics()
    if rendered is None:
        raise HTTPException(status_code=501, detail="prometheus_client 未安装")
    body, content_type = rendered
    from fastapi import Response

    return Response(content=body, media_type=content_type)


@app.middleware("http")
async def prometheus_middleware(request: Request, call_next):
    """请求计数 + 延迟直方图（Prometheus）。SSE 流式响应的延迟口径为
    「首字节前耗时 + 流持续总耗时」（call_next 返回即流建立，体随后台推送）。
    /metrics 自身不计数，避免抓取动作污染业务指标。"""
    import time

    from observability import prom

    started = time.perf_counter()
    response = await call_next(request)
    if request.url.path != "/metrics":
        prom.record_http_request(
            request.method, request.url.path, response.status_code,
            time.perf_counter() - started,
        )
    return response


@app.post("/auth/token")
def issue_token(req: TokenRequest):
    """开发用 token 签发：uid+role 换 JWT。

    仅 AUTH_DEV_MODE=true 且 AUTH_ENABLED=true 时启用（默认关闭）。
    生产环境必须关闭本端点，token 由企业 SSO/OIDC 签发。
    """
    settings = get_settings()
    if not settings.auth_enabled or not settings.auth_dev_mode:
        raise HTTPException(status_code=404, detail="Not Found")
    if not settings.jwt_secret:
        raise HTTPException(status_code=500, detail="服务端认证未配置（JWT_SECRET 缺失）")
    try:
        role = Role(req.role.strip().lower())
    except ValueError:
        raise HTTPException(status_code=400, detail="role 必须是 employee / hr / admin")
    if role == Role.ANONYMOUS:
        raise HTTPException(status_code=400, detail="role 必须是 employee / hr / admin")

    identity = Identity(uid=req.uid, name=req.name, role=role, department=req.department)
    token = encode_token(
        identity,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        expire_minutes=settings.jwt_expire_minutes,
    )
    logger.info("开发模式签发 token：uid=%s role=%s", identity.uid, identity.role.value)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.jwt_expire_minutes * 60,
    }


@app.post("/chat/stream")
def chat_stream(req: ChatRequest, identity: Optional[Identity] = Depends(get_request_identity)):
    # 已认证员工身份以 token 为准（防止请求体伪造他人 uid）；匿名/旁路沿用请求体 uid
    effective_uid = req.uid
    if identity is not None and identity.role == Role.EMPLOYEE and identity.uid:
        effective_uid = identity.uid

    config = {"configurable": {"thread_id": req.thread_id}}

    # 首轮/后续轮状态构造统一走公共层（首轮注入 uid 与熔断计数）
    state = build_turn_state(hr_agent_app, config, effective_uid, req.question)

    logger.info("流式问答请求：thread=%s uid=%s role=%s",
                req.thread_id, effective_uid,
                identity.role.value if identity else "bypass")
    return StreamingResponse(
        _event_stream(state, config, meta={
            "question": req.question, "uid": effective_uid, "channel": "http",
        }, identity=identity),
        media_type="text/event-stream",
    )


@app.post("/chat/resume")
def chat_resume(req: ResumeRequest, identity: Optional[Identity] = Depends(get_request_identity)):
    if req.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision 必须是 approve 或 reject")

    config = {"configurable": {"thread_id": req.thread_id}}
    state_snapshot = hr_agent_app.get_state(config)
    if not state_snapshot.next:
        raise HTTPException(status_code=409, detail="该会话不存在或未处于审批挂起状态")

    # 审批人校验（企业化第二阶段）：角色 + 审批人 ≠ 申请人 + 旧负载兜底；旁路模式不校验
    settings = get_settings()
    applicant_uid = ""
    if settings.auth_enabled:
        interrupt_value = None
        for task in getattr(state_snapshot, "tasks", None) or []:
            if getattr(task, "interrupts", None):
                interrupt_value = task.interrupts[0].value
                break
        applicant_uid = extract_applicant_uid(interrupt_value, state_snapshot.values or {})
        denial = check_approval_allowed(identity, applicant_uid) if identity else None
        if denial or identity is None:
            detail = denial or "身份缺失"
            if identity is not None:
                # 越权/自审自批留痕（denied），不落 PII
                reason = ("self_approval" if "同一人" in detail
                          else "legacy_unverifiable" if "历史会话" in detail else "role")
                audit_approval(req.decision, identity, target_uid=applicant_uid,
                               result="denied", detail=reason)
            logger.warning("审批拦截：thread=%s approver=%s reason=%s",
                           req.thread_id, identity.uid if identity else "bypass", detail)
            raise HTTPException(status_code=403, detail=detail)

    # 审计留痕：审批通过/拒绝（target 取申请人 uid，不落 PII）
    if settings.auth_enabled and identity is not None:
        audit_approval(req.decision, identity, target_uid=applicant_uid)

    logger.info("人工审批恢复：thread=%s decision=%s approver=%s",
                req.thread_id, req.decision, identity.uid if identity else "bypass")
    return StreamingResponse(
        _event_stream(Command(resume=req.decision), config, meta={
            "question": f"[人工审批恢复] {req.decision}", "channel": "http",
        }, identity=identity),
        media_type="text/event-stream",
    )
