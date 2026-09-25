# -*- coding: utf-8 -*-
"""OpenTelemetry 链路追踪接入：OTLP tracer 工厂 + stream_turn span 工具。

静默降级原则（对齐 observability/langfuse.py）：
- OTEL_EXPORTER_OTLP_ENDPOINT 未配置 → get_tracer() 返回 None，主链路零感知
- opentelemetry 包未安装 / 初始化失败 → 记 warning 后返回 None，不抛异常

span 口径（session_runner.stream_turn）：
- span 名："stream_turn"
- 属性：channel（http/streamlit）、uid_hash（sha256 前 12 位，脱敏不落明文 uid）、
  thread_id、latency_s、usage.total_tokens / input_tokens / output_tokens
"""
import hashlib
from functools import lru_cache

from config import get_settings
from logging_config import get_logger

logger = get_logger(__name__)


def hash_uid(uid: str) -> str:
    """uid 脱敏：sha256 前 12 位（可关联同一人多轮会话，不回推明文）。"""
    if not uid:
        return ""
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()[:12]


@lru_cache(maxsize=1)
def get_tracer():
    """构建并缓存 OTLP tracer；未配置或失败时返回 None（静默降级）。

    挂在 session_runner.stream_turn 外围，一个 span 覆盖整轮问答
    （含检索 / 工具调用 / 审计 / 审批挂起检测）。
    """
    settings = get_settings()
    endpoint = (settings.otel_exporter_otlp_endpoint or "").strip()
    if not endpoint:
        return None

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create({"service.name": settings.otel_service_name})
        )
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint.rstrip('/')}/v1/traces"))
        )
        trace.set_tracer_provider(provider)
        logger.info("OpenTelemetry 已启用：OTLP endpoint=%s service=%s",
                    endpoint, settings.otel_service_name)
        return trace.get_tracer("observability.otel")
    except Exception as e:
        logger.warning("OpenTelemetry 初始化失败，静默降级（不影响主链路）：%s", e)
        return None
