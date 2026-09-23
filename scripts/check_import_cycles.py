# -*- coding: utf-8 -*-
"""AST 静态循环 import 检查（CI 语法门禁用，零第三方依赖）。

扫描项目一级包/模块（agent/api/auth/database/tools/eval/observability/
telemetry/mcp_client/mcp_server + 根目录 *.py），构建**模块级**引用图
（仅统计顶层 import——函数体内的延迟导入不计，那是本项目打破循环的
既定手段），DFS 找环。发现循环即以非零退出并打印环路径。

粒度为完整模块名（如 agent.session_runner → telemetry.sink），
而非一级包，避免「telemetry.sink 引 agent.constants 与
agent.session_runner 引 telemetry 被误判为环」这类误报。

运行：python scripts/check_import_cycles.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PACKAGES = [
    "agent", "api", "auth", "database", "tools", "eval",
    "observability", "telemetry", "mcp_client", "mcp_server",
]
ROOT_MODULES = ["config", "logging_config", "streamlit_app", "download_model"]


def iter_project_files() -> dict[str, Path]:
    """模块全名 → 文件路径（包以 __init__ 为代表，子模块为 pkg.sub）。"""
    mods: dict[str, Path] = {}
    for pkg in PACKAGES:
        pkg_dir = ROOT / pkg
        if not pkg_dir.is_dir():
            continue
        for py in pkg_dir.rglob("*.py"):
            rel = py.relative_to(ROOT).with_suffix("")
            parts = list(rel.parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            mods[".".join(parts)] = py
    for mod in ROOT_MODULES:
        py = ROOT / f"{mod}.py"
        if py.exists():
            mods[mod] = py
    return mods


def top_level_imports(path: Path) -> set[str]:
    """模块级（顶层语句）import 的模块名集合（含 from ... import）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    deps: set[str] = set()
    for node in tree.body:  # 只取模块顶层语句，函数/类体内的延迟导入不计
        if isinstance(node, ast.Import):
            for alias in node.names:
                deps.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            deps.add(node.module)
    return deps


def resolve(dep: str, known: set[str]) -> str | None:
    """把 import 目标解析到已知模块：优先全名，退化到其最近已知的父包。"""
    parts = dep.split(".")
    while parts:
        name = ".".join(parts)
        if name in known:
            return name
        parts.pop()
    return None


def build_graph() -> dict[str, set[str]]:
    files = iter_project_files()
    known = set(files)
    graph: dict[str, set[str]] = {m: set() for m in known}
    for mod, path in files.items():
        for dep in top_level_imports(path):
            target = resolve(dep, known)
            if target and target != mod:
                graph[mod].add(target)
    return graph


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    cycles: list[list[str]] = []
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}
    stack: list[str] = []

    # 迭代式 DFS（避免深递归）
    for start in graph:
        if color[start] != WHITE:
            continue
        work = [(start, iter(sorted(graph[start])))]
        color[start] = GRAY
        stack.append(start)
        while work:
            node, it = work[-1]
            advanced = False
            for dep in it:
                if color.get(dep, BLACK) == GRAY:
                    cycles.append(stack[stack.index(dep):] + [dep])
                elif color.get(dep, BLACK) == WHITE:
                    color[dep] = GRAY
                    stack.append(dep)
                    work.append((dep, iter(sorted(graph.get(dep, ())))))
                    advanced = True
                    break
            if not advanced:
                work.pop()
                stack.pop()
                color[node] = BLACK
    return cycles


def main() -> int:
    graph = build_graph()
    cycles = find_cycles(graph)
    if cycles:
        print("发现模块级循环 import：")
        for cyc in cycles:
            print("  " + " -> ".join(cyc))
        return 1
    print(f"无循环 import（分析 {len(graph)} 个模块）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
