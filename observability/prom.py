# -*- coding: utf-8 -*-
"""Prometheus 指标出口：请求计数 / 延迟直方图 / 审计计数器镜像。

设计约定：
- prometheus_client 未安装时全模块静默降级（所有 inc/observe 为 no-op，
  render_metrics 返回 None），不拖垮纯逻辑测试的零依赖特性；
- 审计计数器与 observability/audit.py 同一口径（layer / action 标签），
  由 audit.py 的 record_* 方法镜像写入，AuditCounters 本身逻辑不变；
- 指标名为 hr_agent_ 前缀，直方图桶按秒分级（0.1s ~ 30s，覆盖 SSE 长流式）。
"""
from logging_config import get_logger

logger = get_logger(__name__)

try:
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

    _OK = True

    REQUEST_COUNT = Counter(
        "hr_agent_http_requests_total", "HTTP 请求总数",
        ["method", "endpoint", "status"],
    )
    REQUEST_LATENCY = Histogram(
        "hr_agent_http_request_latency_seconds", "HTTP 请求延迟（秒）",
        ["method", "endpoint"],
        buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, float("inf")),
    )
    AUDIT_BLOCK = Counter(
        "hr_agent_audit_block_total", "审计拦截总数（与 audit.py 同口径）",
        ["layer"],  # rule / llm
    )
    RBAC_DENIED = Counter(
        "hr_agent_rbac_denied_total", "RBAC 越权拦截总数",
        ["action"],  # view_profile / view_leave / issue_cert
    )
    AUDIT_CHECKED = Counter(
        "hr_agent_audit_checked_total", "进入审计的轮次总数",
    )
except ImportError:  # pragma: no cover - 零依赖环境降级
    _OK = False
    logger.warning("prometheus_client 未安装，Prometheus 指标静默降级")


def record_http_request(method: str, endpoint: str, status: int, latency_s: float) -> None:
    """记录一次 HTTP 请求（计数 + 延迟直方图）；未装依赖时为 no-op。"""
    if not _OK:
        return
    REQUEST_COUNT.labels(method=method, endpoint=endpoint, status=str(status)).inc()
    REQUEST_LATENCY.labels(method=method, endpoint=endpoint).observe(latency_s)


def mirror_audit_checked() -> None:
    """镜像 audit.record_checked。"""
    if _OK:
        AUDIT_CHECKED.inc()


def mirror_audit_block(layer: str) -> None:
    """镜像 audit.record_block（layer: rule / llm）。"""
    if _OK:
        AUDIT_BLOCK.labels(layer=layer).inc()


def mirror_rbac_denied(action: str) -> None:
    """镜像 audit.record_access_denied。"""
    if _OK:
        RBAC_DENIED.labels(action=action).inc()


def render_metrics():
    """渲染 /metrics 响应体；(body, content_type)，未装依赖时返回 None。"""
    if not _OK:
        return None
    return generate_latest(), CONTENT_TYPE_LATEST
