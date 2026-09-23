# -*- coding: utf-8 -*-
"""埋点周报命令行入口（cli）。

用法（项目根目录下）：
    python -m telemetry              # 输出最近 7 天 Markdown 周报
    python -m telemetry --days 30    # 自定义统计周期
    python -m telemetry --json       # 输出 JSON 而非 Markdown
    python -m telemetry --init       # 仅初始化数据库表
"""
import argparse
import json
import sys

from config import TELEMETRY_DB
from telemetry.metrics import weekly_report
from telemetry.report import render_markdown
from telemetry.sink import init_db


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
