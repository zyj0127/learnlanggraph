# -*- coding: utf-8 -*-
"""埋点统计（metrics）：按周期聚合 session_events，产出业务指标字典。

指标口径见 telemetry/sink.py 模块头注释（唯一真源）。
"""
import sqlite3
from datetime import datetime, timedelta
from typing import List

from telemetry.sink import _connect, init_db


def _percentile(values: List[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(int(len(ordered) * ratio), len(ordered) - 1)
    return round(ordered[idx], 3)


# 授权事件口径的中文标签（渲染用；事件名以 sink.py 实际写入为准）
AUTH_ACTION_LABELS = {
    "view_profile": "越权查档案",
    "view_leave": "越权查假期",
    "issue_cert": "越权开证明",
    "approval": "人工审批",
    "cert_issued": "证明开具",
}
AUTH_ROLE_LABELS = {
    "anonymous": "匿名",
    "employee": "员工",
    "hr": "HR",
    "admin": "管理员",
}


def auth_security_summary(days: int = 7) -> dict:
    """聚合 auth_events（企业化第二阶段授权审计）：按事件类型 / 角色 / 时间窗计数。

    只聚合 uid 维度之外的计数与动作/角色分布，不输出任何 PII 字段值。
    auth_events 表不存在（旧库未升级）时返回全零结构，不报错。
    """
    init_db()
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    empty = {
        "total": 0,
        "by_action_result": {},
        "by_role": {},
        "approvals": {"approved": 0, "rejected": 0, "denied": 0},
        "access_denied_total": 0,
        "access_denied_top_actions": [],
        "cert_issued": 0,
    }
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT action, actor_role, result FROM auth_events WHERE ts >= ?", (since,))]
        except sqlite3.OperationalError:
            # 旧库无 auth_events 表（第一阶段产物）：按无安全事件处理
            return empty

    summary = dict(empty)
    summary["total"] = len(rows)
    denied_actions: dict = {}
    for r in rows:
        action = r["action"] or "unknown"
        role = r["actor_role"] or "unknown"
        result = r["result"] or "unknown"
        key = f"{action}/{result}"
        summary["by_action_result"][key] = summary["by_action_result"].get(key, 0) + 1
        summary["by_role"][role] = summary["by_role"].get(role, 0) + 1
        if action == "approval":
            if result in summary["approvals"]:
                summary["approvals"][result] += 1
        elif action == "cert_issued":
            summary["cert_issued"] += 1
        elif result == "denied":
            summary["access_denied_total"] += 1
            denied_actions[action] = denied_actions.get(action, 0) + 1
    summary["access_denied_top_actions"] = [
        {"action": action, "count": count}
        for action, count in sorted(denied_actions.items(), key=lambda kv: -kv[1])[:5]
    ]
    return summary


def weekly_report(days: int = 7) -> dict:
    """统计最近 days 天的业务指标（口径见 telemetry/sink.py 模块头注释）。"""
    init_db()
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM session_events WHERE ts >= ? ORDER BY ts DESC", (since,))]

    total = len(rows)
    sessions = {r["session_id"] for r in rows}
    handoff = sum(r["handed_off"] or 0 for r in rows)
    latencies = [r["latency_s"] for r in rows if r["latency_s"]]
    tokens = sum(r["tokens"] or 0 for r in rows)
    cost = sum(r["cost_rmb"] or 0.0 for r in rows)

    by_intent = {}
    for r in rows:
        bucket = by_intent.setdefault(r["intent"] or "其他", {"turns": 0, "handoff": 0})
        bucket["turns"] += 1
        bucket["handoff"] += int(r["handed_off"] or 0)
    for bucket in by_intent.values():
        bucket["handoff_rate"] = round(bucket["handoff"] / bucket["turns"], 4) if bucket["turns"] else 0.0

    badcases = [
        {"ts": r["ts"], "question": r["question"], "intent": r["intent"],
         "reason": r["handoff_reason"] or ("审计打回" if r["audit_rejected"] else "")}
        for r in rows if (r["handed_off"] or r["audit_rejected"])
    ][:10]

    return {
        "period_days": days,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_turns": total,
        "unique_sessions": len(sessions),
        # 口径：转人工率 = 转人工轮次 / 总轮次；自助解决率 = 1 - 转人工率
        "handoff_turns": handoff,
        "handoff_rate": round(handoff / total, 4) if total else 0.0,
        "self_service_rate": round(1 - handoff / total, 4) if total else 0.0,
        "audit_rejected_turns": sum(r["audit_rejected"] or 0 for r in rows),
        "by_intent": by_intent,
        "latency_p50_s": _percentile(latencies, 0.5),
        "latency_p95_s": _percentile(latencies, 0.95),
        "total_tokens": tokens,
        "estimated_cost_rmb": round(cost, 4),
        "top_badcases": badcases,
        # 企业化第二阶段：授权审计（auth_events）聚合，口径见 auth_security_summary
        "auth_security": auth_security_summary(days),
    }
