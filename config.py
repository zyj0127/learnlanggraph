# -*- coding: utf-8 -*-
"""全局配置：统一 Settings（pydantic-settings）+ 路径常量 + LLM 工厂。

设计约定：
- PROJECT_ROOT 只在此处计算一次，全项目（含 observability、telemetry 等）
  统一从这里引用，禁止各处重复 Path(__file__).resolve().parent 拼接。
- Settings 集中管理环境变量（DeepSeek 凭据、模型路径、检索权重、定价等），
  通过 get_settings() 惰性单例访问；模块级仍保留 DOC_PATH / EMPLOYEES_DB /
  CHECKPOINT_DB 等常量与 get_chat_llm() 工厂，保持既有 import 路径兼容。
- 本模块必须保持「轻量」：import 不加载模型、不联网、不创建 LLM 客户端。
"""
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent

load_dotenv(PROJECT_ROOT / ".env")


class Settings(BaseSettings):
    """环境变量统一入口（.env 文件 + 进程环境，进程环境优先）。

    字段名与 .env 中的变量名一一对应（大小写不敏感）。
    未设置的字段回退默认值，保证无 .env 时 import 与测试仍可工作。
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- DeepSeek LLM ----
    deepseek_model_name_chat: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = ""
    # DeepSeek 定价（元/百万 token，估算值，可用环境变量覆盖）
    deepseek_price_input: float = 2.0
    deepseek_price_output: float = 8.0

    # ---- 本地模型路径 ----
    embedding_model: str = ""
    rerank_model: str = ""

    # ---- 运行策略 ----
    hybrid_weights: str = ""
    log_level: str = "INFO"
    # checkpointer 后端：postgres（企业化，审批中断状态入 pg）/ sqlite / memory（评测）
    langgraph_checkpointer: str = "sqlite"

    # ---- 企业化第一阶段：PostgreSQL / pgvector ----
    # 主库连接串（SQLAlchemy / psycopg 风格均可，代码内统一归一化）
    database_url: str = "postgresql+psycopg://hr:hr@localhost:5432/hr_agent"
    # 无 pg 环境的开发回退：True 时实体库走 SQLite、向量库走内存
    use_sqlite_fallback: bool = True
    # 向量库后端：pgvector（入库 kb_chunks 表）/ memory（内存向量库，评测回退）
    vector_store: str = "memory"
    # BGE-small-zh-v1.5 向量维度（pgvector kb_chunks.embedding 列维度）
    embedding_dim: int = 512

    # ---- LLM 网关（OpenAI 兼容协议：可指 DeepSeek 官方或 LiteLLM/OneAPI）----
    # 优先级高于旧 DEEPSEEK_* 三件套；为空时回退旧口径，保持行为不变
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    # ---- 企业化第二阶段：认证授权（auth/ 包）----
    # 总开关：False 时所有鉴权逻辑完全旁路，回到第一阶段行为
    auth_enabled: bool = True
    # 开发模式：仅 True 时启用 POST /auth/token 本地签发端点（生产必须关闭，由企业 SSO 签发）
    auth_dev_mode: bool = False
    # JWT 校验密钥（HS256）；接企业 SSO 时改 RS256 + JWKS（见 auth/jwt_tokens.py 扩展点注释）
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    # 开发签发 token 的有效期（分钟）
    jwt_expire_minutes: int = 120

    # ---- Langfuse 可观测性（未配置时完全静默降级，不影响主链路）----
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"

    # ---- 路径（由 PROJECT_ROOT 派生，唯一真源）----
    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    @property
    def doc_path(self) -> Path:
        return PROJECT_ROOT / "data" / "company_handbook.md"

    @property
    def employees_db(self) -> Path:
        return PROJECT_ROOT / "db" / "employees.db"

    @property
    def checkpoint_db(self) -> Path:
        return PROJECT_ROOT / "db" / "checkpoints.db"

    @property
    def telemetry_db(self) -> Path:
        return PROJECT_ROOT / "db" / "telemetry.db"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回进程级 Settings 单例（首次调用时读取 .env 与环境变量）。"""
    return Settings()


# ---- 数据与产物路径（模块级常量，兼容既有 import）----
DOC_PATH = PROJECT_ROOT / "data" / "company_handbook.md"
EMPLOYEES_DB = PROJECT_ROOT / "db" / "employees.db"
CHECKPOINT_DB = PROJECT_ROOT / "db" / "checkpoints.db"
TELEMETRY_DB = PROJECT_ROOT / "db" / "telemetry.db"


def get_chat_llm(temperature: float = 0.0):
    """创建 Chat 客户端（OpenAI 兼容协议，统一走 LLM 网关口径）。

    网关优先：配置了 LLM_BASE_URL/LLM_API_KEY/LLM_MODEL 时直连网关
    （LiteLLM/OneAPI 或 DeepSeek 官方均可）；未配置时回退旧 DEEPSEEK_* 三件套，
    保持既有部署行为不变。

    温度按用途传入：执行者/审计者 0.0（确定性），查询扩写 0.7（多样性），
    闲置会话总结 0.3（略带归纳弹性）。
    """
    from langchain_openai import ChatOpenAI  # 延迟导入，避免拖累只引用路径的模块

    settings = get_settings()
    # 网关三件套优先，缺哪项回退哪项（兼容只配了 LLM_BASE_URL 指向网关的场景）
    model = settings.llm_model or settings.deepseek_model_name_chat or os.getenv("DEEPSEEK_MODEL_NAME_CHAT")
    api_key = settings.llm_api_key or settings.deepseek_api_key or os.getenv("DEEPSEEK_API_KEY")
    base_url = settings.llm_base_url or settings.deepseek_base_url or os.getenv("DEEPSEEK_BASE_URL")
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
    )
