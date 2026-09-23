# -*- coding: utf-8 -*-
"""当前请求身份上下文（contextvars 实现）。

方案对齐项目既有约定：与 LLM 用量/埋点一样走「请求级隐式上下文」，
调用方（FastAPI 依赖、Streamlit、session_runner）在请求入口 set，
工具层在执行时 get，业务函数签名不增加身份参数，保持既有调用面不变。

contextvars 对 asyncio（SSE 流式）与线程（Streamlit 脚本线程）都安全，
每个请求独立的上下文副本互不串扰。
"""
from contextvars import ContextVar, Token
from typing import Optional

from auth.models import ANONYMOUS_IDENTITY, Identity

# 当前请求身份；None 表示未显式设置（get 时回落为匿名身份）
_CURRENT_IDENTITY: ContextVar[Optional[Identity]] = ContextVar(
    "hr_agent_current_identity", default=None
)


def set_current_identity(identity: Optional[Identity]) -> Token:
    """设置当前请求身份，返回 Token 供 reset（请求结束务必复位，防泄漏到下一请求）。"""
    return _CURRENT_IDENTITY.set(identity)


def reset_current_identity(token: Token) -> None:
    """按 Token 复位身份上下文（与 set_current_identity 配对使用）。

    容错：FastAPI/Starlette 的 SSE 场景下，同步生成器可能在 anyio 的
    另一个 Context 里被关闭（close/GC），此时 Token 跨 Context 复位会抛
    ValueError。回落为在当前 Context 显式置 None（匿名语义），身份绝不
    滞留为上一个请求的值——安全语义不变。
    """
    try:
        _CURRENT_IDENTITY.reset(token)
    except ValueError:
        _CURRENT_IDENTITY.set(None)


def get_current_identity() -> Identity:
    """取当前请求身份；未设置时返回匿名身份（最低权限，安全优先）。"""
    identity = _CURRENT_IDENTITY.get()
    if identity is None:
        return ANONYMOUS_IDENTITY
    return identity
