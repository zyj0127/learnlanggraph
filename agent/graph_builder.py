# -*- coding: utf-8 -*-
"""HR 智能助理 LangGraph 装配入口。

模块拆分：
- agent/state.py      状态定义（AgentState）
- agent/constants.py  前后端共享的协议常量
- agent/nodes.py      节点实现（执行者 / 人工审批 / 事实审计）
- agent/routers.py    条件路由

本文件只负责：建图、挂 checkpointer、导出编译产物 hr_agent_app。
"""
import os
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from agent.nodes import ALL_TOOLS, chatbot_node, fact_check_node, human_review_node
from agent.routers import router_after_chatbot, router_after_fact_check, router_after_review
from agent.state import AgentState
from config import CHECKPOINT_DB


def _build_checkpointer():
    """持久化 checkpointer：多轮记忆落盘，服务重启后对话记忆不丢失。

    评测/测试可设环境变量 LANGGRAPH_CHECKPOINTER=memory 使用内存版，避免污染生产库。
    """
    if os.getenv("LANGGRAPH_CHECKPOINTER", "sqlite") == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()

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
