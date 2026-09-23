# -*- coding: utf-8 -*-
"""
飞羽科技 HR 智能助理 —— Streamlit 前端
======================================
仅做前端展示层：Graph 驱动、首轮状态构造、审批恢复与埋点统一走
agent/session_runner.py（与 FastAPI 服务共用同一实现与埋点口径）。
直接复用 agent.graph_builder 中编译好的 hr_agent_app（LangGraph）。

运行方式（务必在项目根目录下启动）：
    streamlit run streamlit_app.py
"""
import uuid

import streamlit as st

from logging_config import get_logger

logger = get_logger(__name__)

from langgraph.types import Command

from agent.constants import IDLE_TIMEOUT_CMD
from agent.session_runner import build_turn_state, stream_turn

# ---------------------------------------------------------------------------
# 1. 加载 LangGraph 应用（重量级：加载 embedding / reranker / 编译图，缓存一次）
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner="首次加载需要加载本地模型与知识库，请稍候…")
def load_hr_agent():
    from agent.graph_builder import hr_agent_app

    return hr_agent_app


# ---------------------------------------------------------------------------
# 2. 会话状态初始化
# ---------------------------------------------------------------------------
def new_thread_id(uid: str) -> str:
    return f"session_{uid}_{uuid.uuid4().hex[:8]}"


def init_session_state():
    if "uid" not in st.session_state:
        st.session_state.uid = "1001"
    if "thread_id" not in st.session_state:
        st.session_state.thread_id = new_thread_id(st.session_state.uid)
    # 展示层消息列表：[{"role": "user"/"assistant"/"tool", ...}]
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    # 登录身份（auth.models.Identity）；None = 匿名（仅政策问答可用，
    # 涉及个人数据的工具会被 RBAC 拦下）；AUTH_ENABLED=false 时恒为 None（旁路）
    if "identity" not in st.session_state:
        st.session_state.identity = None


def reset_conversation(uid: str):
    """开启全新会话：新的 thread_id（新的记忆线程）+ 清空展示历史"""
    st.session_state.thread_id = new_thread_id(uid)
    st.session_state.chat_history = []


# ---------------------------------------------------------------------------
# 3. 消息渲染辅助
# ---------------------------------------------------------------------------
def render_tool_call(tool_call: dict, container):
    """渲染一次工具调用（名称 + 参数）"""
    name = tool_call.get("name", "unknown_tool")
    args = tool_call.get("args", {})
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
    with container:
        with st.expander(f"🔧 调用工具：`{name}({args_str})`", expanded=False):
            st.json(args)


def render_tool_result(name: str, content: str, container):
    """渲染工具返回结果"""
    with container:
        with st.expander(f"📄 工具返回：`{name}`", expanded=False):
            st.text(content)


def _stream_agent(payload, config: dict, show_trace: bool, label: str, meta: dict = None,
                  identity=None):
    """
    驱动后端 Graph 执行（首轮输入或审批恢复指令），并以 token 级流式渲染。

    Graph 驱动与埋点由 agent/session_runner.py 统一实现；本函数只负责
    把统一事件流渲染成 Streamlit 组件（工具调用轨迹 + 流式答案）。

    payload:  首轮为 state dict，审批恢复为 Command(resume=...)。
    meta:     埋点字段（question / uid / channel），统一落 db/telemetry.db。
    identity: 当前登录身份（auth.models.Identity），由 session_runner 设置进
              contextvars 供工具层 RBAC 校验；None 表示匿名/旁路。
    返回 (final_text, interrupted_info)；interrupted_info 非空表示 Graph
    在 human_review 节点被 interrupt 挂起。
    """
    app = load_hr_agent()
    final_text = ""
    interrupted_info = None
    answer_placeholder = st.empty()  # 最终答案的流式占位（位于聊天消息区内）

    with st.status(label, expanded=show_trace) as status:
        current_msg_id = None
        current_answer = ""
        seen_tool_ids = set()

        for event in stream_turn(app, payload, config, meta=meta, identity=identity):
            event_type = event["type"]

            if event_type == "tool_call":
                # 工具调用阶段：属于「思考/决策」，清空此前流出的叙事文本
                current_answer = ""
                answer_placeholder.empty()
                tool_call = event["tool_call"]
                tc_id = tool_call.get("id")
                if tc_id not in seen_tool_ids:
                    seen_tool_ids.add(tc_id)
                    render_tool_call(tool_call, status)
                    status.update(
                        label=f"🔧 正在调用工具 `{tool_call.get('name', '')}` …"
                    )

            elif event_type == "tool_result":
                render_tool_result(event["name"], event["content"], status)

            elif event_type == "token":
                # 最终答案 token：chatbot 节点内容（已在公共层过滤）
                if event["msg_id"] != current_msg_id:
                    current_msg_id = event["msg_id"]
                    current_answer = ""
                current_answer += event["content"]
                final_text = current_answer
                answer_placeholder.markdown(current_answer + " ▌")

            elif event_type == "approval_required":
                interrupted_info = event["interrupt_value"]

        if interrupted_info is not None:
            status.update(label="⏸️ 敏感操作等待人工审批…", state="complete", expanded=False)
        else:
            status.update(label="✅ 处理完成", state="complete", expanded=False)

    # 移除流式光标，定格最终答案
    if final_text:
        answer_placeholder.markdown(final_text)
    else:
        answer_placeholder.empty()

    return final_text, interrupted_info


def run_agent(user_input: str, uid: str, thread_id: str, show_trace: bool):
    """
    驱动后端 Graph 跑一轮，并以 token 级流式渲染最终答案。
    返回最终用于展示的 assistant 文本。
    若 Graph 在 human_review 节点被 interrupt 挂起，则把审批信息写入
    st.session_state.pending_approval 并返回空字符串。
    """
    config = {"configurable": {"thread_id": thread_id}}
    app = load_hr_agent()
    # 首轮/后续轮状态构造统一走公共层（首轮注入 uid 与熔断计数 loop_state）
    state = build_turn_state(app, config, uid, user_input)

    final_text, interrupted_info = _stream_agent(
        state, config, show_trace, "🤖 HR 助理思考中…",
        meta={"question": user_input, "uid": uid, "channel": "streamlit"},
        identity=st.session_state.get("identity"),
    )

    if interrupted_info is not None:
        st.session_state.pending_approval = {
            "thread_id": thread_id,
            "info": interrupted_info,
            # 申请人 uid（审批恢复时校验审批人 ≠ 申请人）
            "applicant_uid": uid,
        }

    return final_text


def resume_agent(decision: str, thread_id: str, show_trace: bool):
    """
    人工审批后恢复被 interrupt 挂起的 Graph 执行，并流式渲染续答内容。
    decision: 'approve'（放行执行工具）或 'reject'（拒绝并安抚用户）。
    """
    config = {"configurable": {"thread_id": thread_id}}
    label = "✅ 已批准，继续执行…" if decision == "approve" else "❌ 已拒绝，继续执行…"
    identity = st.session_state.get("identity")
    # 审批留痕：审批人身份与申请人身份分离（只记 uid/角色/决定，不落 PII）
    if identity is not None:
        from auth.guard import audit_approval

        audit_approval(decision, identity, target_uid=st.session_state.uid)
    final_text, _ = _stream_agent(
        Command(resume=decision), config, show_trace, label,
        meta={"question": f"[人工审批恢复] {decision}", "channel": "streamlit"},
        identity=identity,
    )
    return final_text


# ---------------------------------------------------------------------------
# 4. 页面布局
# ---------------------------------------------------------------------------
st.set_page_config(page_title="飞羽科技 HR 智能助理", page_icon="🪶", layout="centered")
init_session_state()

with st.sidebar:
    st.title("🪶 飞羽科技 HR 助理")
    st.caption("基于 LangGraph · RAG · 事实审计")

    uid_options = {
        "1001": "1001 · 张三（P5 北京）",
        "1002": "1002 · 李四（P4 成都）",
        "1003": "1003 · 王五（P7 上海）",
        "1004": "1004 · 赵六（P3 深圳）",
    }

    # ---- 登录身份（企业化第二阶段：RBAC；AUTH_ENABLED=false 时旁路）----
    from auth.models import Identity, Role
    from config import get_settings

    _auth_enabled = get_settings().auth_enabled
    _role_labels = {Role.EMPLOYEE: "员工", Role.HR: "HR 专员", Role.ADMIN: "管理员"}

    st.subheader("登录身份")
    if not _auth_enabled:
        # 鉴权旁路：保持旧的「员工身份」切换行为
        selected_uid = st.selectbox(
            "当前登录员工",
            options=list(uid_options.keys()),
            format_func=lambda u: uid_options[u],
            index=list(uid_options.keys()).index(st.session_state.uid),
        )
        if selected_uid != st.session_state.uid:
            st.session_state.uid = selected_uid
            reset_conversation(selected_uid)
            st.rerun()
    elif st.session_state.identity is None:
        st.caption("未登录（匿名）：仅可咨询政策类问题，个人数据功能需登录")
        login_uid = st.selectbox(
            "员工账号",
            options=list(uid_options.keys()),
            format_func=lambda u: uid_options[u],
        )
        login_role = st.selectbox(
            "角色",
            options=list(_role_labels.keys()),
            format_func=lambda r: _role_labels[r],
        )
        if st.button("🔑 登录", use_container_width=True):
            st.session_state.identity = Identity(
                uid=login_uid, name=uid_options[login_uid].split("·")[1].strip().split("（")[0],
                role=login_role,
            )
            st.session_state.uid = login_uid
            reset_conversation(login_uid)
            st.rerun()
    else:
        identity = st.session_state.identity
        st.success(f"已登录：{identity.name or identity.uid}（{_role_labels.get(identity.role, identity.role.value)}）")
        if st.button("🚪 退出登录", use_container_width=True):
            st.session_state.identity = None
            reset_conversation(st.session_state.uid)
            st.rerun()

    st.subheader("会话")
    st.text(f"thread_id: {st.session_state.thread_id}")
    show_trace = st.toggle("显示执行轨迹（工具调用详情）", value=True)

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🆕 开启新会话", use_container_width=True):
            reset_conversation(st.session_state.uid)
            st.rerun()
    with col2:
        if st.button("🧹 清空显示", use_container_width=True):
            st.session_state.chat_history = []
            st.rerun()

    if st.button("⏱️ 生成闲置会话总结", use_container_width=True,
                 help="模拟用户长时间未操作，让助理总结本次会话的核心问题与结论"):
        if not st.session_state.chat_history:
            st.warning("当前还没有对话内容，无法总结。")
        else:
            with st.chat_message("assistant"):
                summary = run_agent(
                    IDLE_TIMEOUT_CMD,
                    st.session_state.uid,
                    st.session_state.thread_id,
                    show_trace=False,
                )
                if summary:
                    st.session_state.chat_history.append(
                        {"role": "assistant", "content": summary}
                    )

    st.divider()
    st.caption("💡 能力：员工档案查询 / 假期余额 / 在职·收入证明 / 员工手册政策问答")

st.title("HR 智能助理")
_identity = st.session_state.get("identity")
_identity_label = (
    f"**{_identity.name or _identity.uid}**（{_role_labels.get(_identity.role, '')}）"
    if _identity else "**匿名访客**（仅政策问答）"
)
st.caption(
    f"当前身份：{_identity_label} ｜ "
    "可咨询考勤假期、差旅报销等政策，或查询个人档案、假期余额、开具证明"
)

# ---------------------------------------------------------------------------
# 5. 渲染历史对话
# ---------------------------------------------------------------------------
for item in st.session_state.chat_history:
    with st.chat_message(item["role"]):
        st.markdown(item["content"])

# ---------------------------------------------------------------------------
# 5.5 挂起的人工审批（human_review 节点 interrupt 等待授权）
# ---------------------------------------------------------------------------
pending = st.session_state.get("pending_approval")
if pending and pending.get("thread_id") == st.session_state.thread_id:
    with st.chat_message("assistant"):
        # 开启鉴权后 interrupt 负载为 dict（message + applicant_uid），旧会话为纯字符串
        _info = pending["info"]
        _info_text = _info.get("message", "") if isinstance(_info, dict) else str(_info)
        st.warning(f"🔒 **敏感操作需要人工审批**\n\n{_info_text}")
        # 审批人校验：角色 + 审批人 ≠ 申请人 + 旧负载兜底（旁路模式不校验）
        from auth.guard import check_approval_allowed, extract_applicant_uid

        _applicant_uid = (
            extract_applicant_uid(_info) or pending.get("applicant_uid", "")
        )
        _approval_denial = None
        if _auth_enabled:
            _approval_denial = (
                check_approval_allowed(_identity, _applicant_uid)
                if _identity is not None else "权限提示：请先以 HR 或管理员身份登录后再审批。"
            )
        _can_approve = _approval_denial is None
        if not _can_approve:
            st.info(_approval_denial)
        col1, col2 = st.columns(2)
        with col1:
            approve_clicked = st.button("✅ 批准执行", use_container_width=True,
                                        disabled=not _can_approve)
        with col2:
            reject_clicked = st.button("❌ 拒绝执行", use_container_width=True,
                                       disabled=not _can_approve)

        if approve_clicked or reject_clicked:
            decision = "approve" if approve_clicked else "reject"
            del st.session_state["pending_approval"]
            try:
                answer = resume_agent(decision, st.session_state.thread_id, show_trace)
            except Exception as e:  # 前端兜底，不影响后端
                logger.exception("审批恢复执行出错")
                answer = ""
                st.error(f"审批恢复执行出错：{e}")

            if not answer:
                answer = "本次操作已处理完毕，如需其他帮助请继续提问。"
            st.session_state.chat_history.append(
                {"role": "assistant", "content": answer}
            )
            st.rerun()
elif pending:
    # 审批挂起属于旧会话（已切换 thread），直接丢弃避免卡死
    del st.session_state["pending_approval"]

# ---------------------------------------------------------------------------
# 6. 处理用户输入
# ---------------------------------------------------------------------------
if prompt := st.chat_input("请输入您的问题，例如：我还有多少天年假？"):
    st.session_state.chat_history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            answer = run_agent(
                prompt,
                st.session_state.uid,
                st.session_state.thread_id,
                show_trace=show_trace,
            )
        except Exception as e:  # 前端兜底，不影响后端
            logger.exception("调用后端服务出错")  # 完整堆栈写入日志与控制台
            answer = ""
            st.error(f"调用后端服务出错：{type(e).__name__}: {e}")

        if answer:
            # 答案已由 run_agent 流式渲染，这里只需写入历史
            st.session_state.chat_history.append(
                {"role": "assistant", "content": answer}
            )
        elif st.session_state.get("pending_approval"):
            # Graph 在人工审批处挂起：重绘页面以显示审批按钮，不写兜底文案
            st.rerun()
        else:
            fallback = "抱歉，本轮没有生成有效答复，请换个问法再试一次。"
            st.markdown(fallback)
            st.session_state.chat_history.append(
                {"role": "assistant", "content": fallback}
            )
