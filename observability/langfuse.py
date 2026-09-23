# -*- coding: utf-8 -*-
"""Langfuse 可观测性接入：LangChain callback handler 工厂。

与既有 observability/usage.py（用量统计）、telemetry/（埋点）并存，互不替代。

静默降级原则（对齐「埋点失败不影响主链路」）：
- langfuse_enabled = False 或密钥未配置 → 返回 None，主链路零感知
- langfuse 包未安装 / 初始化失败 → 记 warning 后返回 None，不抛异常
"""
from config import get_settings
from logging_config import get_logger

logger = get_logger(__name__)


def get_langfuse_handler():
    """构建 Langfuse LangChain CallbackHandler；未启用或失败时返回 None。

    挂在 Graph 执行的 config["callbacks"] 上（session_runner 统一注入），
    trace 粒度为整轮问答，span 覆盖各节点与 LLM 调用。
    """
    settings = get_settings()
    if not settings.langfuse_enabled:
        return None
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        logger.warning("LANGFUSE_ENABLED=true 但缺少密钥配置，Langfuse 静默降级")
        return None

    try:
        from langfuse.langchain import CallbackHandler  # 重依赖，延迟导入

        return CallbackHandler(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception as e:
        logger.warning("Langfuse 初始化失败，静默降级（不影响主链路）：%s", e)
        return None
