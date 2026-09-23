# -*- coding: utf-8 -*-
"""埋点报告渲染（report）：把统计结果渲染成可直接贴进汇报的 Markdown。"""


def render_markdown(report: dict) -> str:
    """把周报渲染成可直接贴进汇报的 markdown。"""
    lines = [
        f"# HR 助理运营周报（最近 {report['period_days']} 天）",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 总轮次：{report['total_turns']} ｜ 会话数：{report['unique_sessions']}",
        f"- 转人工轮次：{report['handoff_turns']}（转人工率 {report['handoff_rate']:.1%}）"
        f" ｜ 自助解决率 {report['self_service_rate']:.1%}",
        f"- 审计打回轮次：{report['audit_rejected_turns']}",
        f"- 延迟 P50 / P95：{report['latency_p50_s']}s / {report['latency_p95_s']}s",
        f"- Token 总量：{report['total_tokens']} ｜ 估算成本：¥{report['estimated_cost_rmb']}",
        "",
        "## 按意图分布",
        "",
        "| 意图 | 轮次 | 转人工 | 转人工率 |",
        "| --- | --- | --- | --- |",
    ]
    for intent, bucket in sorted(report["by_intent"].items(), key=lambda kv: -kv[1]["turns"]):
        lines.append(f"| {intent} | {bucket['turns']} | {bucket['handoff']} | {bucket['handoff_rate']:.1%} |")

    lines += ["", "## Top Badcase（转人工 / 审计打回）", ""]
    if report["top_badcases"]:
        for item in report["top_badcases"]:
            lines.append(f"- `{item['ts']}` [{item['intent']}] {item['question']} — 原因：{item['reason']}")
    else:
        lines.append("- 本周期无")

    # 企业化第二阶段：安全与审计（auth_events 聚合；无数据时兜底文案，不报错）
    lines += _render_auth_security(report.get("auth_security"))
    return "\n".join(lines) + "\n"


def _render_auth_security(auth: dict) -> list:
    """渲染「安全与审计」小节：授权事件按类型/角色分布 + 审批与越权计数。"""
    from telemetry.metrics import AUTH_ACTION_LABELS, AUTH_ROLE_LABELS

    lines = ["", "## 安全与审计（授权事件）", ""]
    if not auth or not auth.get("total"):
        lines.append("- 本周期无安全事件。")
        return lines

    approvals = auth.get("approvals", {})
    lines.append(
        f"- 授权事件总数：{auth['total']} ｜ 越权拦截：{auth.get('access_denied_total', 0)}"
        f" ｜ 审批 通过/拒绝/拦截：{approvals.get('approved', 0)}/{approvals.get('rejected', 0)}/{approvals.get('denied', 0)}"
        f" ｜ 证明开具：{auth.get('cert_issued', 0)}"
    )

    lines += ["", "| 事件类型 | 次数 |", "| --- | --- |"]
    for key, count in sorted(auth.get("by_action_result", {}).items(), key=lambda kv: -kv[1]):
        action, _, result = key.partition("/")
        label = AUTH_ACTION_LABELS.get(action, action)
        lines.append(f"| {label}（{result}） | {count} |")

    if auth.get("by_role"):
        role_text = "、".join(
            f"{AUTH_ROLE_LABELS.get(role, role)} {count}"
            for role, count in sorted(auth["by_role"].items(), key=lambda kv: -kv[1])
        )
        lines += ["", f"- 按操作人角色分布：{role_text}"]

    top = auth.get("access_denied_top_actions") or []
    if top:
        lines.append("- 越权 Top 动作：" + "、".join(
            f"{AUTH_ACTION_LABELS.get(item['action'], item['action'])} {item['count']} 次"
            for item in top
        ))
    return lines
