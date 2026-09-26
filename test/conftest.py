# -*- coding: utf-8 -*-
"""pytest 公共配置：导入路径 + 重依赖兜底 + marker 门禁机制。

1. 导入路径：把项目根目录加入 sys.path，统一替代各测试文件顶部的
   sys.path.insert 样板（unittest 直接运行时无需本文件）。

2. 重依赖兜底（collect_ignore）：部分测试模块在 import 期就依赖
   langchain_core / fastapi 等重包。零依赖环境（如 CI 语法门禁 job、
   贡献者未装全依赖的本地环境）下不让 collection 报错，而是整模块跳过；
   装全依赖后自动恢复收集。纯逻辑套件（fact_rules / permissions /
   auth_rbac / auth_metrics / eval_gate 的 GT 与契约部分）保持零依赖可跑。

3. marker 门禁（默认跳过，环境变量显式开启）：
   - needs_models：需要 BGE 模型权重（约 3.4GB，CI_MODEL_TESTS=1 开启）
   - needs_pg：    需要 PostgreSQL 实例（CI_PG_TESTS=1 开启）
   - needs_llm：   需要真实 LLM API Key（CI_LLM_TESTS=1 开启，消耗 token）
   marker 在 pyproject.toml [tool.pytest.ini_options] markers 中注册。
"""
import importlib.util
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# 重依赖模块的 collect_ignore：import 期缺依赖的模块整体跳过（不报错）
# ---------------------------------------------------------------------------
_HAS_LANGCHAIN = importlib.util.find_spec("langchain_core") is not None
_HAS_FASTAPI = importlib.util.find_spec("fastapi") is not None
_HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None

collect_ignore: list[str] = []
if not _HAS_LANGCHAIN:
    # 模块顶部即 import langchain_core / rag_pipeline（后者依赖 langchain）
    collect_ignore += [
        "test_milestone1.py",
        "test_milestone2.py",
        "test_milestone3.py",
        "test_milestone4.py",
    ]
if not (_HAS_FASTAPI and _HAS_LANGGRAPH):
    # test_api / test_admin_queue 在测试方法内 import fastapi.testclient 与 api.server（依赖 langgraph）
    collect_ignore.append("test_api.py")
    collect_ignore.append("test_admin_queue.py")

# ---------------------------------------------------------------------------
# marker 门禁：默认跳过，CI 有对应资源时经环境变量开启
# ---------------------------------------------------------------------------
_MARKER_GATES = {
    "needs_models": ("CI_MODEL_TESTS", "需要 BGE 模型权重（CI_MODEL_TESTS=1 开启）"),
    "needs_pg": ("CI_PG_TESTS", "需要 PostgreSQL 实例（CI_PG_TESTS=1 开启）"),
    "needs_llm": ("CI_LLM_TESTS", "需要真实 LLM API Key（CI_LLM_TESTS=1 开启，消耗 token）"),
}


def pytest_collection_modifyitems(config, items):
    import pytest

    skips = {
        marker: pytest.mark.skip(reason=reason)
        for marker, (env, reason) in _MARKER_GATES.items()
        if os.environ.get(env) != "1"
    }
    if not skips:
        return
    for item in items:
        for marker, skip_mark in skips.items():
            if item.get_closest_marker(marker) is not None:
                item.add_marker(skip_mark)
