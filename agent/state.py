# -*- coding: utf-8 -*-
"""LangGraph 全局共享状态定义。"""
from typing import Annotated, List, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """全局共享状态。

    - messages:    对话消息列表（add_messages  reducer 自动追加/去重）
    - current_uid: 当前登录员工 uid（应用层注入）
    - loop_state:  执行者节点被调用的轮次，用于反思熔断
    """
    messages: Annotated[List[BaseMessage], add_messages]
    current_uid: str
    loop_state: int
