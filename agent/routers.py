# -*- coding: utf-8 -*-
"""LangGraph 条件路由：决定每个节点执行完毕后的去向。"""
from langchain_core.messages import HumanMessage, ToolMessage

from agent.constants import HANDOFF_PREFIX, MAX_REFLECTION_LOOPS
from agent.state import AgentState
from logging_config import get_logger

logger = get_logger(__name__)

__all__ = ["router_after_chatbot", "router_after_review", "router_after_fact_check"]


def router_after_chatbot(state: AgentState) -> str:
    """Chatbot 输出后的路由判断。"""
    last_message = state["messages"][-1]

    # 转人工兜底消息直接结束，跳过审计（审计只针对政策问答）
    if (getattr(last_message, "content", "") or "").startswith(HANDOFF_PREFIX):
        return "end"

    if hasattr(last_message, "tool_calls") and last_message.tool_calls:
        return "human_review"
    return "fact_checker"


def router_after_review(state: AgentState) -> str:
    """审批节点后的流向判断。"""
    last_message = state["messages"][-1]
    if isinstance(last_message, ToolMessage):
        return "chatbot"
    return "tools"


def router_after_fact_check(state: AgentState) -> str:
    """审计完成后的路由判断。"""
    last_message = state["messages"][-1]
    if isinstance(last_message, HumanMessage):
        if state.get("loop_state", 0) > MAX_REFLECTION_LOOPS:
            logger.warning("强制熔断：反思次数超上限，放弃重写")
            return "end"
        logger.info("审计未通过，打回 chatbot 重写")
        return "chatbot"
    return "end"
