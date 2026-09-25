# -*- coding: utf-8 -*-
"""审计指标计数器：幻觉治理效果的可度量口径（线程安全实现）。

字段口径：
    checked          本进程内进入审计的轮次数（分母）
    passed           审计通过数
    rule_blocked     规则层拦截数（数字类幻觉，零 token 成本）
    llm_blocked      模型层拦截数（语义类幻觉）
    parse_errors     审计输出解析失败次数（fail-safe 触发，不放行）
    fallback_handoff 熔断后转人工兜底次数
    access_denied    RBAC 越权拦截次数（企业化第二阶段，layer 记为 "rbac"）
    block_reasons    拦截原因样本（用于人工复核与误杀分析）

线程安全：
    FastAPI 多 worker 线程 / Streamlit 脚本线程会并发进入审计节点，
    裸 `counter.checked += 1` 不是原子操作。本实现用 threading.Lock 保护所有
    读写路径；对外只暴露 record_* / reset / snapshot 方法，不再暴露裸字段自增。
"""
import threading
from typing import Dict, List

from observability import prom  # prometheus_client 缺失时内部静默降级


class AuditCounters:
    """审计节点运行指标，供评测脚本与周报计算拦截率/误杀率。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reset_locked()

    # ---- 内部实现（调用方必须已持锁）----
    def _reset_locked(self) -> None:
        self._checked = 0
        self._passed = 0
        self._rule_blocked = 0
        self._llm_blocked = 0
        self._parse_errors = 0
        self._fallback_handoff = 0
        self._access_denied = 0
        self._block_reasons: List[Dict[str, str]] = []

    # ---- 记录接口（审计节点专用）----
    def record_checked(self) -> None:
        """一轮进入审计。"""
        with self._lock:
            self._checked += 1
        prom.mirror_audit_checked()

    def record_passed(self) -> None:
        """一轮审计通过。"""
        with self._lock:
            self._passed += 1

    def record_parse_error(self) -> None:
        """审计输出解析失败（fail-safe 触发）。"""
        with self._lock:
            self._parse_errors += 1

    def record_fallback_handoff(self) -> None:
        """熔断后转人工兜底。"""
        with self._lock:
            self._fallback_handoff += 1

    def record_access_denied(self, action: str) -> None:
        """RBAC 越权拦截（企业化第二阶段）：action 为 view_profile / view_leave / issue_cert。"""
        with self._lock:
            self._access_denied += 1
            if len(self._block_reasons) < 50:
                self._block_reasons.append({"layer": "rbac", "reason": action})
        prom.mirror_rbac_denied(action)

    def record_block(self, layer: str, reason: str) -> None:
        """记录一次拦截：layer 为 "rule"（规则层）或 "llm"（模型层）。"""
        with self._lock:
            if layer == "rule":
                self._rule_blocked += 1
            else:
                self._llm_blocked += 1
            if len(self._block_reasons) < 50:
                self._block_reasons.append({"layer": layer, "reason": reason})
        prom.mirror_audit_block("rule" if layer == "rule" else "llm")

    # ---- 读取接口 ----
    def reset(self) -> None:
        with self._lock:
            self._reset_locked()

    def snapshot(self) -> dict:
        """当前指标的一致性快照（持锁拷贝，评测/周报读取专用）。"""
        with self._lock:
            blocked = self._rule_blocked + self._llm_blocked
            return {
                "checked": self._checked,
                "passed": self._passed,
                "blocked": blocked,
                "rule_blocked": self._rule_blocked,
                "llm_blocked": self._llm_blocked,
                "parse_errors": self._parse_errors,
                "fallback_handoff": self._fallback_handoff,
                "access_denied": self._access_denied,
                "block_rate": round(blocked / self._checked, 4) if self._checked else 0.0,
                "block_reasons": list(self._block_reasons),
            }


# 进程级单例：evaluate.py 与 benchmark.py 可直接 import 读取
AUDIT_COUNTERS = AuditCounters()
