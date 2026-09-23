# -*- coding: utf-8 -*-
"""会话埋点包：把每一轮问答落成结构化事件，支撑自助解决率等业务指标。

模块拆分（原 telemetry.py 一身四职，重构后各司其职）：
- telemetry/sink.py     埋点写入：schema、意图分类、log_turn（指标口径唯一真源）
- telemetry/metrics.py  统计聚合：weekly_report
- telemetry/report.py   渲染：render_markdown
- telemetry/cli.py      命令行：python -m telemetry [--days N] [--json] [--init]

兼容 shim：旧的 `from telemetry import log_turn, weekly_report, render_markdown`
等 import 路径保持不变。
"""
from telemetry.metrics import weekly_report
from telemetry.report import render_markdown
from telemetry.sink import (
    INTENT_RULES,
    TELEMETRY_DB,
    classify_intent,
    extract_retrieved,
    init_db,
    log_auth_event,
    log_turn,
)

__all__ = [
    "INTENT_RULES",
    "TELEMETRY_DB",
    "classify_intent",
    "extract_retrieved",
    "init_db",
    "log_auth_event",
    "log_turn",
    "render_markdown",
    "weekly_report",
]
