# -*- coding: utf-8 -*-
"""HR 智能助理 LangGraph 装配入口。

模块拆分：
- agent/state.py      状态定义（AgentState）
- agent/constants.py  前后端共享的协议常量
- agent/nodes.py      节点实现（执行者 / 人工审批 / 事实审计）
- agent/routers.py    条件路由

本文件只负责：建图、挂 checkpointer、导出编译产物 hr_agent_app。

checkpointer 后端（Settings.langgraph_checkpointer / 环境变量 LANGGRAPH_CHECKPOINTER）：
- postgres  企业化主路径：审批 interrupt 状态持久化到 PostgreSQL
- sqlite    默认：本地 db/checkpoints.db（行为与重构前一致）
- memory    评测/测试：内存版，不污染任何持久库
"""
import os
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from agent.nodes import ALL_TOOLS, chatbot_node, fact_check_node, human_review_node
from agent.routers import router_after_chatbot, router_after_fact_check, router_after_review
from agent.state import AgentState
from config import CHECKPOINT_DB, get_settings
from logging_config import get_logger

logger = get_logger(__name__)


def _build_postgres_checkpointer():
    """PostgreSQL checkpointer：审批挂起状态入 pg，多实例部署可共享会话。

    连接串复用 DATABASE_URL（归一化为 psycopg 协议串）；连接对象挂在 saver 上
    保持与进程同生命周期（PostgresSaver 不拥有连接，需防 GC 提前关闭）。
    """
    import psycopg  # 重依赖，延迟导入
    from langgraph.checkpoint.postgres import PostgresSaver

    url = get_settings().database_url
    # 剥离 SQLAlchemy 驱动声明，得到纯 psycopg 连接串
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            url = "postgresql://" + url[len(prefix):]
            break
    conn = psycopg.connect(url, autocommit=True)
    saver = PostgresSaver(conn)
    saver.setup()  # 幂等：建 checkpoints 等内部表
    saver._conn_keepalive = conn  # noqa: SLF001 防连接被回收
    logger.info("LangGraph checkpointer：PostgreSQL（%s）", url.split("@")[-1])
    return saver


def _build_checkpointer():
    """持久化 checkpointer：多轮记忆落盘，服务重启后对话记忆不丢失。

    评测/测试可设 LANGGRAPH_CHECKPOINTER=memory 使用内存版，避免污染生产库。
    """
    backend = (os.getenv("LANGGRAPH_CHECKPOINTER")
               or get_settings().langgraph_checkpointer or "sqlite").strip().lower()

    if backend == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()

    if backend == "postgres":
        try:
            return _build_postgres_checkpointer()
        except Exception as e:
            # 与项目「外围能力失败不影响主链路」原则一致：pg 不可用时回退 sqlite
            logger.warning("PostgreSQL checkpointer 初始化失败，回退 SQLite：%s", e)

    conn = sqlite3.connect(str(CHECKPOINT_DB), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


# ---- 建图 ----
workflow = StateGraph(AgentState)

workflow.add_node("chatbot", chatbot_node)
workflow.add_node("human_review", human_review_node)
workflow.add_node("tools", ToolNode(ALL_TOOLS))
workflow.add_node("fact_checker", fact_check_node)

workflow.add_edge(START, "chatbot")
workflow.add_conditional_edges(
    "chatbot",
    router_after_chatbot,
    {"human_review": "human_review", "fact_checker": "fact_checker", "end": END},
)
workflow.add_conditional_edges(
    "human_review",
    router_after_review,
    {"chatbot": "chatbot", "tools": "tools"},
)
workflow.add_edge("tools", "chatbot")
workflow.add_conditional_edges(
    "fact_checker",
    router_after_fact_check,
    {"chatbot": "chatbot", "end": END},
)

hr_agent_app = workflow.compile(checkpointer=_build_checkpointer())
