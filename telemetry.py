# -*- coding: utf-8 -*-
"""会话级埋点：把每一轮问答落成结构化事件，让「自助解决率」这类业务指标有可核实的数据源。

为什么需要它
------------
「拦截率 80%」这种数字如果没有埋点支撑，就只是估算，一旦被追问统计口径就会露。
本模块把每轮问答的关键字段落盘，指标口径写在代码里，避免口径漂移。

指标口径（唯一真源，改口径必须同步改这里与对外材料）
--------------------------------------------------
- 自助解决率 = 1 - 转人工轮次 / 总轮次；粒度 = 轮次级；周期 = 自然周（可配天数）
- 转人工判定：该轮最终回答以 HANDOFF_PREFIX 开头（情绪兜底、超纲转接、审计熔断兜底）
- 命中率类指标由 eval/ 下的评测脚本负责，本表只记业务字段

数据落盘：db/telemetry.db（SQLite），与 LangGraph 的 checkpoints.db 分离，互不影响。
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from agent.constants import AUDIT_FAIL_PREFIX, HANDOFF_PREFIX
from config import PROJECT_ROOT
from logging_config import get_logger

logger = get_logger(__name__)

TELEMETRY_DB = PROJECT_ROOT / "db" / "telemetry.db"

# 意图分类：轻量关键词规则（可解释、零成本、可离线评估；后续换分类模型时字段口径不变）
INTENT_RULES = (
    ("假期", ("年假", "请假", "事假", "病假", "婚假", "产假", "陪产", "育儿假", "调休", "加班", "假期")),
    ("报销", ("报销", "差旅", "出差", "住宿", "补贴", "发票", "招待", "团建")),
    ("证明", ("证明", "盖章", "电子章", "工单")),
    ("薪酬", ("工资", "薪资", "薪酬", "绩效", "调薪", "社保", "公积金", "个税", "奖金", "工资单")),
    ("福利", ("体检", "福利", "保险", "积分", "慰问", "心理咨询")),
    ("行政", ("工牌", "门禁", "会议室", "工位", "快递", "办公用品", "资产", "访客")),
    ("手续", ("入职", "转正", "离职", "交接", "转岗", "试用期", "实习")),
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,           -- ISO 时间戳
    session_id      TEXT    NOT NULL,           -- 会话 ID（thread_id）
    uid             TEXT,                       -- 员工 uid
    channel         TEXT,                       -- http / streamlit
    question        TEXT,                       -- 本轮问题
    intent          TEXT,                       -- 意图分类（规则）
    retrieved       TEXT,                       -- 检索命中的章节（JSON 数组）
    handed_off      INTEGER DEFAULT 0,          -- 是否转人工
    handoff_reason  TEXT,                       -- 转人工原因（情绪/超纲/审计熔断）
    audit_rejected  INTEGER DEFAULT 0,          -- 本轮是否被审计打回
    latency_s       REAL,                       -- 端到端耗时
    llm_calls       INTEGER,                    -- LLM 调用次数
    tokens          INTEGER,                    -- token 总量
    cost_rmb        REAL,                       -- 估算成本
    feedback        TEXT                        -- 用户反馈（up / down / 文本）
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON session_events(ts);
CREATE INDEX IF NOT EXISTS idx_events_session ON session_events(session_id);
"""


def _connect() -> sqlite3.Connection:
    TELEMETRY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(TELEMETRY_DB), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)


def classify_intent(question: str) -> str:
    """轻量意图分类：命中关键词即归类，全部未命中归为「其他」。"""
    text = question or ""
    for intent, keywords in INTENT_RULES:
        if any(kw in text for kw in keywords):
            return intent
    return "其他"


def extract_retrieved(messages) -> list:
    """从 RAG 工具返回中解析命中的章节路径（形如 `来源 1: 第2章 > 2.2 住宿标准`）。"""
    sections = []
    for message in reversed(list(messages or [])):
        if getattr(message, "name", "") == "search_hr_policy":
            for line in (message.content or "").splitlines():
                if line.startswith("来源 "):
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        sections.append(parts[1].strip())
            break
    return sections


def log_turn(*, session_id: str, question: str, uid: str = "", channel: str = "http",
             final_messages=None, usage: dict = None, latency_s: float = None,
             feedback: str = None) -> int:
    """记录一轮问答。返回事件 id（失败不抛异常，埋点不得影响主链路）。"""
    try:
        messages = list(final_messages or [])
        last_text = getattr(messages[-1], "content", "") if messages else ""
        handed_off = last_text.startswith(HANDOFF_PREFIX)
        if handed_off:
            reason = last_text[len(HANDOFF_PREFIX):].split("。")[0].strip() or "转人工"
        else:
            reason = ""
        audit_rejected = any(
            (getattr(m, "content", "") or "").startswith(AUDIT_FAIL_PREFIX) for m in messages
        )

        usage = usage or {}
        row = (
            datetime.now().isoformat(timespec="seconds"), session_id, uid, channel, question,
            classify_intent(question),
            json.dumps(extract_retrieved(messages), ensure_ascii=False),
            int(handed_off), reason, int(audit_rejected),
            round(latency_s, 3) if latency_s is not None else None,
            usage.get("llm_calls"), usage.get("total_tokens"), usage.get("estimated_cost_rmb"),
            feedback,
        )
        with _connect() as conn:
            cur = conn.execute(
                "INSERT INTO session_events (ts, session_id, uid, channel, question, intent,"
                " retrieved, handed_off, handoff_reason, audit_rejected, latency_s, llm_calls,"
                " tokens, cost_rmb, feedback) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                row,
            )
            return int(cur.lastrowid)
    except Exception as exc:  # 埋点失败不影响用户体验
        logger.warning("埋点写入失败：%s", exc)
        return -1


def _percentile(values: list, ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(int(len(ordered) * ratio), len(ordered) - 1)
    return round(ordered[idx], 3)


def weekly_report(days: int = 7) -> dict:
    """统计最近 days 天的业务指标（口径见模块头注释）。"""
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
    }


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
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="会话埋点周报")
    parser.add_argument("--days", type=int, default=7, help="统计周期（自然日，默认 7）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而非 markdown")
    parser.add_argument("--init", action="store_true", help="仅初始化数据库表")
    args = parser.parse_args()

    if args.init:
        init_db()
        print(f"埋点表已初始化：{TELEMETRY_DB}")
        return

    report = weekly_report(args.days)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report))


if __name__ == "__main__":
    sys.exit(main())
