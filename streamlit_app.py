# -*- coding: utf-8 -*-
"""
飞羽科技 HR 智能助理 —— Streamlit 前端
======================================
仅做前端展示层，不修改任何后端代码。
直接复用 agent.graph_builder 中编译好的 hr_agent_app（LangGraph）。

运行方式（务必在项目根目录下启动）：
    streamlit run streamlit_app.py
"""
import os
import sys
import uuid
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# 0. 环境准备：保证 .env 与包导入路径与后端脚本一致
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

from logging_config import get_logger  # noqa: E402

logger = get_logger(__name__)

from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage  # noqa: E402
from langgraph.types import Command  # noqa: E402

from agent.constants import IDLE_TIMEOUT_CMD  # noqa: E402

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


def _stream_agent(payload, config: dict, show_trace: bool, label: str):
    """
    驱动后端 Graph 执行（首轮输入或审批恢复指令），并以 token 级流式渲染。

    payload: 首轮为 state dict，审批恢复为 Command(resume=...)。
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

        for chunk, metadata in app.stream(payload, config, stream_mode="messages"):
            node = metadata.get("langgraph_node", "")

            if isinstance(chunk, AIMessageChunk):
                # 工具调用阶段：属于「思考/决策」，清空此前流出的叙事文本
                if chunk.tool_calls:
                    current_answer = ""
                    answer_placeholder.empty()
                    for tc in chunk.tool_calls:
                        tc_id = tc.get("id")
                        if tc_id not in seen_tool_ids:
                            seen_tool_ids.add(tc_id)
                            render_tool_call(tc, status)
                            status.update(
                                label=f"🔧 正在调用工具 `{tc.get('name', '')}` …"
                            )
                    continue

                # 最终答案 token：仅 chatbot 节点、非工具调用的内容
                if node == "chatbot" and chunk.content:
                    if chunk.id != current_msg_id:
                        current_msg_id = chunk.id
                        current_answer = ""
                    current_answer += chunk.content
                    final_text = current_answer
                    answer_placeholder.markdown(current_answer + " ▌")

            elif isinstance(chunk, ToolMessage):
                render_tool_result(chunk.name or "tool", str(chunk.content), status)

        # 兜底：messages 流不直接透出 interrupt，改查图状态
        snapshot = app.get_state(config)
        if snapshot.next:
            for task in snapshot.tasks:
                if task.interrupts:
                    interrupted_info = task.interrupts[0].value
                    break

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
    state = {
        "messages": [HumanMessage(content=user_input)],
        "current_uid": uid,
        "loop_state": 0,
    }

    final_text, interrupted_info = _stream_agent(
        state, config, show_trace, "🤖 HR 助理思考中…"
    )

    if interrupted_info is not None:
        st.session_state.pending_approval = {
            "thread_id": thread_id,
            "info": interrupted_info,
        }

    return final_text


def resume_agent(decision: str, thread_id: str, show_trace: bool):
    """
    人工审批后恢复被 interrupt 挂起的 Graph 执行，并流式渲染续答内容。
    decision: 'approve'（放行执行工具）或 'reject'（拒绝并安抚用户）。
    """
    config = {"configurable": {"thread_id": thread_id}}
    label = "✅ 已批准，继续执行…" if decision == "approve" else "❌ 已拒绝，继续执行…"
    final_text, _ = _stream_agent(Command(resume=decision), config, show_trace, label)
    return final_text


# ---------------------------------------------------------------------------
# 4. 页面布局
# ---------------------------------------------------------------------------
st.set_page_config(page_title="飞羽科技 HR 智能助理", page_icon="🪶", layout="centered")
init_session_state()

with st.sidebar:
    st.title("🪶 飞羽科技 HR 助理")
    st.caption("基于 LangGraph · RAG · 事实审计")

    st.subheader("员工身份")
    uid_options = {
        "1001": "1001 · 张三（P5 北京）",
        "1002": "1002 · 李四（P4 成都）",
        "1003": "1003 · 王五（P7 上海）",
        "1004": "1004 · 赵六（P3 深圳）",
    }
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
st.caption(
    f"当前员工：**{uid_options[st.session_state.uid]}** ｜ "
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
        st.warning(f"🔒 **敏感操作需要人工审批**\n\n{pending['info']}")
        col1, col2 = st.columns(2)
        with col1:
            approve_clicked = st.button("✅ 批准执行", use_container_width=True)
        with col2:
            reject_clicked = st.button("❌ 拒绝执行", use_container_width=True)

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
