# -*- coding: utf-8 -*-
"""HR 实体查询工具的 stdio MCP Server。

把 tools/hr_tools.py 的 3 个员工档案/假期/证明开具工具按 MCP（Model Context
Protocol）标准对外暴露：任何 MCP 客户端（Claude Desktop、Cursor、LangGraph
Agent 经 langchain-mcp-adapters 等）都可以即插即用地调用这 3 个工具。

实现方式：**逻辑零复制**——server 端直接复用 @tool 装饰器内的原始函数
（LangChain 工具对象的 `.func` 属性），保证「直连路径」与「MCP 路径」的
业务逻辑严格同源，A/B 一致性校验（mcp_client/ab_check.py）才能逐字节对齐。

启动（stdio 传输，供 MCP 客户端以子进程方式拉起）：
    python mcp_server/hr_tools_server.py

客户端接入示例（LangGraph 侧）：
    from langchain_mcp_adapters.client import MultiServerMCPClient
    client = MultiServerMCPClient({
        "hr_tools": {
            "command": r"E:\\File\\virtualenv\\test_py02\\Scripts\\python.exe",
            "args": [r"E:\\code\\py\\learnlanggraph\\mcp_server\\hr_tools_server.py"],
            "transport": "stdio",
        }
    })
    tools = await client.get_tools()
"""
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# stdio 子进程方式被拉起时 cwd 不在项目根，手动把项目根放进 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.hr_tools import (  # noqa: E402
    get_employee_profile,
    get_leave_balance,
    generate_employment_certification,
)

mcp = FastMCP("hr-tools")


@mcp.tool()
def employee_profile(uid: str) -> str:
    """根据员工uid查询员工的完整人事档案，包括姓名，职级，工作城市，入职年限，基本薪资。
    当需要获取当前对话的员工的属性时，必须调用此工具
    """
    return get_employee_profile.func(uid)


@mcp.tool()
def leave_balance(uid: str) -> str:
    """根据员工uid 查询剩余假期余额（年假和病假）
    当员工明确提问"我还有几天假"或我的余额时调用
    """
    return get_leave_balance.func(uid)


@mcp.tool()
def employment_certification(uid: str, cer_type: str) -> str:
    """为员工生成指定类型的证明文件。
    参数cer_type必须是以下两个值之一
    -'employment'：仅开具在职证明
    -'income'：开具包含薪资的在职收入证明（有职级权限限制）
    """
    return generate_employment_certification.func(uid, cer_type)


if __name__ == "__main__":
    mcp.run(transport="stdio")
