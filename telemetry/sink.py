# -*- coding: utf-8 -*-
"""埋点写入（sink）：会话级事件落盘 SQLite，指标口径的唯一真源。

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
import json
import sqlite3
from datetime import datetime
from typing import List, Optional

from agent.constants import AUDIT_FAIL_PREFIX, HANDOFF_PREFIX
from config import TELEMETRY_DB
from logging_config import get_logger

logger = get_logger(__name__)

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

-- 企业化第二阶段：认证授权审计留痕（越权拦截 / 审批决定 / 证明开具）。
-- 只记操作人 uid/角色/目标 uid/动作/结果，不落 PII 敏感字段值（姓名、薪资等）。
CREATE TABLE IF NOT EXISTS auth_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,           -- ISO 时间戳
    action      TEXT    NOT NULL,           -- view_profile / view_leave / issue_cert / approval / cert_issued
    actor_uid   TEXT,                       -- 操作人 uid（匿名记 "anonymous"）
    actor_role  TEXT,                       -- 操作人角色（anonymous/employee/hr/admin）
    target_uid  TEXT,                       -- 目标员工 uid
    result      TEXT,                       -- denied / approved / rejected / success
    detail      TEXT                        -- 附加说明（如证明类型，不含 PII 值）
);
CREATE INDEX IF NOT EXISTS idx_auth_events_ts ON auth_events(ts);
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


def extract_retrieved(messages) -> List[str]:
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
             final_messages=None, usage: Optional[dict] = None, latency_s: Optional[float] = None,
             feedback: Optional[str] = None) -> int:
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


def log_auth_event(*, action: str, actor_uid: str, actor_role: str,
                   target_uid: str = "", result: str, detail: str = "") -> int:
    """记录一条认证授权审计事件（越权拦截 / 审批决定 / 证明开具）。

    口径：只记 uid/角色/动作/结果等标识字段，不落姓名、薪资等 PII 值。
    返回事件 id；失败不抛异常（埋点不得影响主链路）。
    """
    try:
        init_db()  # 幂等：确保 auth_events 表已建（兼容第一阶段已存在的 telemetry.db）
        row = (
            datetime.now().isoformat(timespec="seconds"), action,
            actor_uid, actor_role, target_uid, result, detail,
        )
        with _connect() as conn:
            cur = conn.execute(
                "INSERT INTO auth_events (ts, action, actor_uid, actor_role, target_uid,"
                " result, detail) VALUES (?,?,?,?,?,?,?)",
                row,
            )
            return int(cur.lastrowid)
    except Exception as exc:  # 埋点失败不影响用户体验
        logger.warning("授权审计埋点写入失败：%s", exc)
        return -1
