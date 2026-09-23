# -*- coding: utf-8 -*-
"""MCP 路径 vs 直连路径的 A/B 一致性校验。

对同一组入参，分别通过两条路径调用 HR 工具：
- 直连路径：import tools/hr_tools.py 的 @tool 对象（进程内函数调用）；
- MCP 路径：langchain-mcp-adapters 以 stdio 子进程拉起 mcp_server/hr_tools_server.py，
  经 MCP 协议远程调用。

逐条断言两条路径的返回完全一致，证明「抽成 MCP Server」没有改变任何业务行为——
Agent 可以在不停机的前提下把工具层迁移到 MCP 架构。

运行：
    python mcp_client/ab_check.py
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langchain_mcp_adapters.client import MultiServerMCPClient  # noqa: E402

from tools.hr_tools import (  # noqa: E402
    generate_employment_certification,
    get_employee_profile,
    get_leave_balance,
)

VENV_PYTHON = sys.executable

# A/B 用例：正常查询 + 边界（不存在的 uid / 越权证明 / 非法证明类型）
CASES = [
    ("get_employee_profile", {"uid": "1001"}),
    ("get_employee_profile", {"uid": "9999"}),
    ("get_leave_balance", {"uid": "1003"}),
    ("get_leave_balance", {"uid": "8888"}),
    ("generate_employment_certification", {"uid": "1001", "cer_type": "employment"}),
    ("generate_employment_certification", {"uid": "1002", "cer_type": "income"}),
    ("generate_employment_certification", {"uid": "1005", "cer_type": "income"}),
    ("generate_employment_certification", {"uid": "1001", "cer_type": "hacker"}),
]

DIRECT_TOOLS = {
    "get_employee_profile": get_employee_profile,
    "get_leave_balance": get_leave_balance,
    "generate_employment_certification": generate_employment_certification,
}


def _mcp_text(mcp_out) -> str:
    """langchain-mcp-adapters 返回 content block 列表，提取其中的纯文本。"""
    if isinstance(mcp_out, str):
        return mcp_out
    if isinstance(mcp_out, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in mcp_out
        )
    return str(mcp_out)


async def main() -> int:
    client = MultiServerMCPClient({
        "hr_tools": {
            "command": VENV_PYTHON,
            "args": [str(PROJECT_ROOT / "mcp_server" / "hr_tools_server.py")],
            "transport": "stdio",
        }
    })
    mcp_tools = {t.name: t for t in await client.get_tools()}
    # MCP server 端工具名：employee_profile / leave_balance / employment_certification
    name_map = {
        "get_employee_profile": "employee_profile",
        "get_leave_balance": "leave_balance",
        "generate_employment_certification": "employment_certification",
    }
    missing = [name_map[c[0]] for c in CASES if name_map[c[0]] not in mcp_tools]
    if missing:
        print(f"[fail] MCP server 未暴露预期工具：{missing}")
        return 1

    print(f"{'用例':<52s}  直连=MCP?")
    failed = 0
    for tool_name, kwargs in CASES:
        direct_out = DIRECT_TOOLS[tool_name].invoke(kwargs)
        mcp_out = _mcp_text(await mcp_tools[name_map[tool_name]].ainvoke(kwargs))
        ok = (direct_out == mcp_out)
        failed += int(not ok)
        case = f"{tool_name}({', '.join(f'{k}={v!r}' for k, v in kwargs.items())})"
        print(f"{case:<52s}  {'√ 一致' if ok else '× 不一致'}")
        if not ok:
            print(f"    直连: {direct_out!r}")
            print(f"    MCP : {mcp_out!r}")

    print(f"\n[{'pass' if failed == 0 else 'fail'}] {len(CASES) - failed}/{len(CASES)} 条用例双路径返回一致")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
