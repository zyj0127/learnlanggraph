# -*- coding: utf-8 -*-
"""全局配置：项目路径、环境变量、LLM 工厂。

所有模块统一从这里取路径、创建 LLM 实例，
避免各处重复 load_dotenv() 与重复拼接 PROJECT_ROOT。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent

load_dotenv(PROJECT_ROOT / ".env")

# ---- 数据与产物路径 ----
DOC_PATH = PROJECT_ROOT / "data" / "company_handbook.md"
EMPLOYEES_DB = PROJECT_ROOT / "db" / "employees.db"
CHECKPOINT_DB = PROJECT_ROOT / "db" / "checkpoints.db"


def get_chat_llm(temperature: float = 0.0):
    """创建 DeepSeek Chat 客户端。

    温度按用途传入：执行者/审计者 0.0（确定性），查询扩写 0.7（多样性），
    闲置会话总结 0.3（略带归纳弹性）。
    """
    from langchain_openai import ChatOpenAI  # 延迟导入，避免拖累只引用路径的模块

    return ChatOpenAI(
        model=os.getenv("DEEPSEEK_MODEL_NAME_CHAT"),
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url=os.getenv("DEEPSEEK_BASE_URL"),
        temperature=temperature,
    )
