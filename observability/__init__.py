# -*- coding: utf-8 -*-
"""本地可观测性包：LLM 用量采集 + 审计指标计数器。

模块拆分：
- observability/usage.py   UsageTracker（LangChain callback：token / 延迟 / 成本，
                           定价常量统一由 config.Settings 提供）
- observability/audit.py   AuditCounters（幻觉审计运行指标，threading.Lock 线程安全）

兼容 shim：旧的 `from observability import UsageTracker, AUDIT_COUNTERS` 写法不变。
PEP 562 惰性导出：audit.py 不依赖 langchain_core，纯逻辑测试可直接
`from observability.audit import AUDIT_COUNTERS`，不被 usage.py 的重依赖拖累。
"""
__all__ = ["AUDIT_COUNTERS", "AuditCounters", "UsageTracker"]


def __getattr__(name: str):
    if name in ("AUDIT_COUNTERS", "AuditCounters"):
        from . import audit

        return getattr(audit, name)
    if name == "UsageTracker":
        from .usage import UsageTracker

        return UsageTracker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
