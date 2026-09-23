# -*- coding: utf-8 -*-
"""RAG 检索管线：查询扩写 + HyDE + 混合检索（BM25 + 向量）+ CrossEncoder 重排。

向量库使用内存版（不落盘）：知识库规模小（个位数章节、不到百个 chunk），
进程启动时现场 embedding 不到 2 秒，彻底规避 ChromaDB 持久化 SQLite 在 Windows 上
「unable to open database file / 单写者锁 / 进程强杀损坏」这一整类问题。

懒加载约定（企业级重构）
----------------------
本模块 **import 必须轻量**：不加载 BGE 模型、不构建向量库、不创建 LLM 客户端、不联网。
所有重资源（embeddings / reranker / 扩写 LLM / retriever）均由 lru_cache 工厂函数
在首次使用时构建并缓存：

- get_embeddings()    BGE 嵌入模型
- get_reranker()      BGE Rerank 重排模型
- get_expansion_llm() 查询扩写专用 LLM
- get_retriever()     BM25 + 向量混合检索器（内部触发 embeddings 与向量库构建）

历史兼容：模块级属性 embeddings / reranker / llm / retriever 通过 PEP 562
__getattr__ 惰性解析，旧的 `from agent.rag_pipeline import retriever` 写法仍然可用
（但会在该语句执行时触发加载，新代码请改用 get_retriever() 显式获取）。
"""
import os
from functools import lru_cache
from typing import Any, List

from langchain_core.documents import Document
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.retrievers import BaseRetriever
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent.chunking import split_handbook
from config import DOC_PATH, get_chat_llm, get_settings
from logging_config import get_logger

logger = get_logger(__name__)

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL")
RERANK_MODEL = os.getenv("RERANK_MODEL")


# ---- 1. 核心组件懒加载工厂 ----
@lru_cache(maxsize=1)
def get_embeddings():
    """加载并缓存 BGE 嵌入模型（首次调用时加载）。"""
    from langchain_huggingface import HuggingFaceEmbeddings  # 重依赖，延迟导入

    logger.info("正在加载 BGE 嵌入模型")
    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )


@lru_cache(maxsize=1)
def get_reranker():
    """加载并缓存 BGE Rerank 重排模型（首次调用时加载）。"""
    from sentence_transformers import CrossEncoder  # 重依赖，延迟导入

    logger.info("正在加载 BGE Rerank 重排模型")
    return CrossEncoder(RERANK_MODEL, max_length=512, device="cpu")


@lru_cache(maxsize=1)
def get_expansion_llm():
    """查询扩写专用 LLM：稍高的温度（0.7）换取改写多样性。"""
    return get_chat_llm(temperature=0.7)


# ---- 混合召回权重（可配置，便于离线扫参与线上回滚）----
# 顺序约定为 (向量, BM25)，必须与 EnsembleRetriever 的 retrievers 顺序保持一致。
# 默认值来自 eval/weight_sweep.py 在 709 题政策评测集（2026.09-v3，69 chunk 知识库）上的扫参结果，
# 完整数据见 eval/weight_sweep_result.json：
#     向量0.1 / 0.15 / 0.4 → Hit@3  9.73%   ← 0.4 为旧默认，扩库后 BM25 侧排序严重拖累 Top-3
#     向量0.5 / BM25 0.5  → Hit@3 79.13%
#     向量0.6 / BM25 0.4  → Hit@3 84.91%   ← 当前默认
#     向量0.85 / BM25 0.15 → Hit@3 84.91%
#     纯向量              → Hit@3 85.19%
#     纯 BM25             → Hit@3  8.60%
# 可用环境变量覆盖，例如 HYBRID_WEIGHTS=0.5,0.5 或 HYBRID_WEIGHTS=1,0 做对照实验。
# 调参纪律：先跑 eval/weight_sweep.py 拿整体 Hit@3 对照，禁止只凭单点 badcase 改权重。
DEFAULT_HYBRID_WEIGHTS = (0.6, 0.4)


def _resolve_weights() -> list:
    """从环境变量 HYBRID_WEIGHTS 解析权重，非法输入回退默认值。"""
    raw = (os.getenv("HYBRID_WEIGHTS") or "").strip()
    if not raw:
        return list(DEFAULT_HYBRID_WEIGHTS)
    try:
        weights = [float(x) for x in raw.split(",")]
    except ValueError:
        logger.warning("HYBRID_WEIGHTS 解析失败（%s），回退默认值 %s", raw, DEFAULT_HYBRID_WEIGHTS)
        return list(DEFAULT_HYBRID_WEIGHTS)
    if len(weights) != 2 or any(w < 0 for w in weights) or sum(weights) <= 0:
        logger.warning("HYBRID_WEIGHTS 值非法（%s），回退默认值 %s", raw, DEFAULT_HYBRID_WEIGHTS)
        return list(DEFAULT_HYBRID_WEIGHTS)
    return weights


# ---- 2. 构建多路召回 Retriever ----
def _build_memory_vectorstore(splits: List[Document]):
    """构建内存版向量库（不落盘）——默认/回退路径，行为与重构前一致。"""
    from langchain_chroma import Chroma  # 重依赖，延迟导入

    logger.info("知识库构建：生成内存向量库（不落盘）")
    return Chroma.from_documents(documents=splits, embedding=get_embeddings())


class _PgVectorRetriever(BaseRetriever):
    """pgvector 向量检索器：余弦距离 Top-K，返回与内存版相同的 Document 结构。

    仅作为 EnsembleRetriever 的向量腿使用，检索口径（k=5）与内存路径一致。
    """

    k: int = 5

    def _get_relevant_documents(self, query: str) -> List[Document]:
        from database.kb_store import search_by_embedding  # pg 依赖，延迟导入

        query_vec = get_embeddings().embed_query(query)
        rows = search_by_embedding(query_vec, k=self.k)
        return [Document(page_content=r["content"], metadata=r["meta"]) for r in rows]


def _ensure_pgvector_index(splits: List[Document]) -> None:
    """幂等灌入 kb_chunks：表为空时现场 embedding 并批量写入。

    表缺失（-1）时抛错提示先跑迁移——建表是 Alembic 的职责，运行期不隐式建表。
    """
    from database.kb_store import count_chunks, upsert_chunks  # pg 依赖，延迟导入

    count = count_chunks()
    if count > 0:
        logger.info("pgvector 索引已存在（%d 条），跳过重建", count)
        return
    if count < 0:
        raise RuntimeError("kb_chunks 表不存在，请先执行：alembic upgrade head")

    logger.info("kb_chunks 为空，从 %s 重建 pgvector 索引（%d 个 chunk）", DOC_PATH, len(splits))
    embeddings = get_embeddings()
    vectors = embeddings.embed_documents([d.page_content for d in splits])
    upsert_chunks(
        contents=[d.page_content for d in splits],
        metadatas=[d.metadata for d in splits],
        embeddings=vectors,
    )


def _build_vector_retriever(splits: List[Document]):
    """按 Settings.vector_store 选择向量腿：pgvector（企业化）/ memory（默认回退）。"""
    backend = (get_settings().vector_store or "memory").strip().lower()
    if backend == "pgvector":
        _ensure_pgvector_index(splits)
        logger.info("向量检索后端：pgvector（kb_chunks 表）")
        return _PgVectorRetriever(k=5)
    return _build_memory_vectorstore(splits).as_retriever(search_kwargs={"k": 5})


def build_ensemble_retriever():
    """构建 BM25 + 向量混合检索器。

    切块策略（结构感知优先，表格结构化提取，递归字符兜底；实现见 agent/chunking.py）：
    1. 先按 Markdown 标题层级（## / ###）切分，让一个条款/小节成为一个完整语义单元；
    2. 小节内的 Markdown 表格解析为「一行一条记录」的独立切片（自带表头语义、
       KV 元数据与行号，引用可精确到「表格第 N 行」）；
    3. 仅当某切片仍超过 500 字符时，才用 RecursiveCharacterTextSplitter 按段落、
       换行两级分隔符降级切分，块长 500、重叠 50（约 10%），降低条件句被切断的概率；
       重叠会带来重复召回与索引膨胀，检索侧用内容级去重对冲（见 search_hr_policy）。
    元数据保留 Chapter / Section 路径（表格切片另有 table_row / table_kv），用于引用溯源。
    """
    from langchain_classic.retrievers import EnsembleRetriever  # 重依赖，延迟导入
    from langchain_community.retrievers import BM25Retriever

    if not DOC_PATH.exists():
        raise FileNotFoundError(f"找不到知识库文件{DOC_PATH}")

    with open(DOC_PATH, "r", encoding="utf-8") as f:
        markdown_text = f.read()

    # 与 eval/weight_sweep.py 共用同一切分实现，保证离线扫参与线上检索同源
    splits = split_handbook(markdown_text)

    logger.info("文档切分完毕，共生成 %d 个 chunk", len(splits))

    # 路线 A：全文关键词检索（BM25）
    bm25_retriever = BM25Retriever.from_documents(splits)
    bm25_retriever.k = 5

    # 路线 B：向量语义检索（内存库 / pgvector，由 Settings.vector_store 切换）
    vector_retriever = _build_vector_retriever(splits)

    # 混合：EnsembleRetriever 加权融合（权重可配置，见 _resolve_weights）
    weights = _resolve_weights()
    logger.info("混合检索权重（向量, BM25）= %s", weights)
    ensemble_retriever = EnsembleRetriever(
        retrievers=[vector_retriever, bm25_retriever],
        weights=weights,
    )
    return ensemble_retriever


@lru_cache(maxsize=1)
def get_retriever():
    """构建并缓存混合检索器（首次调用时触发模型加载与向量库构建）。"""
    logger.info("正在构建混合检索（BM25 + 向量）")
    return build_ensemble_retriever()


# ---- 3. 智能扩写与 HyDE ----
class QueryExpansion(BaseModel):
    expanded_queries: List[str] = Field(description="从不同维度扩写3个相关检索词或短语")
    hypothetical_document: str = Field(description="针对该问题一段假设性，看似专业的官方制度回答片段（允许伪造数字）")


expansion_parser = JsonOutputParser(pydantic_object=QueryExpansion)


def expand_and_hyde(original_query: str) -> list[str]:
    """LLM 生成多维度扩写与 HyDE 假设文档。"""
    prompt = ChatPromptTemplate.from_template(
        "你是一名专业的企业 HR 专家。为了提高知识库检索命中率，请协助处理用户的原始提问。\n"
        "任务 1（多维扩展）：站在不同视角（如政策名词、审批流程、系统操作）扩写 3 个相关检索词或短语。\n"
        "任务 2（HyDE假设）：用官方、严谨的 HR 规章制度口吻，伪造一段回答该问题的文本。不管事实是否正确，重点是极度模仿'员工手册'"
        "的专业行文风格和词汇分布。\n\n"
        "用户原始问题：{query}\n\n"
        "{format_instructions}"
    )
    chain = prompt | get_expansion_llm() | expansion_parser
    try:
        result = chain.invoke({
            "query": original_query,
            "format_instructions": expansion_parser.get_format_instructions(),
        })
        logger.debug("原始问题：%s", original_query)
        logger.debug("衍生查询：%s", result["expanded_queries"])
        logger.debug("HyDE 伪文：%s", result["hypothetical_document"][:30])

        # 汇总：原始问题 + 3 个衍生查询 + 1 个假设性文档
        return [original_query] + result["expanded_queries"] + [result["hypothetical_document"]]

    except Exception as e:
        logger.warning("查询扩写 LLM 调用失败，降级使用基础检索：%s", e)
        return [original_query]


# ---- 4. 封装工具 ----
@tool
def search_hr_policy(query: str) -> str:
    """
    高级知识搜索引擎（具备自动改写、混合检索、重排功能）。
    当用户询问任何关于公司规章制度、差旅报销标准、假期政策、福利等相关信息，必须调用此工具。
    输入参数 query 必须是用户原始问题
    """
    retriever = get_retriever()
    reranker = get_reranker()

    # 步骤一：获取 5 个查询变体组成的查询矩阵
    search_queries = expand_and_hyde(query)

    # 步骤二：多路并发检索（BM25 + Vector）
    all_candidate_docs = []
    for q in search_queries:
        docs = retriever.invoke(q)
        all_candidate_docs.extend(docs)

    # 步骤三：文档去重（以文档内容作为唯一标识）
    unique_docs = list({doc.page_content: doc for doc in all_candidate_docs}.values())

    if not unique_docs:
        return "知识库中未检索到相关政策，请提示用户询问 HR 人工。"

    # 步骤四：Cross-Encoder（交叉编码器）精准重排
    # 必须用用户「原始真实问题」去和召回的文档计算相关性得分
    sentence_pairs = [[query, doc.page_content] for doc in unique_docs]
    scores = reranker.predict(sentence_pairs)

    scored_docs = list(zip(unique_docs, scores))
    # 按模型打分从高到低排序
    scored_docs.sort(key=lambda x: x[1], reverse=True)

    # 步骤五：截取真正的 Top-3 并组装返回文本
    top_3_docs = [doc for doc, _ in scored_docs[:3]]

    context_parts = []
    for i, doc in enumerate(top_3_docs, 1):
        chapter = doc.metadata.get("Chapter", "未知章节")
        section = doc.metadata.get("Section", "未知段落")
        context_parts.append(f"来源 {i}: {chapter} > {section} \n {doc.page_content}")

    merged_context = "\n\n".join(context_parts)

    return f"「知识库检索结果」\n{merged_context}"


# ---- 历史兼容：模块级惰性属性（PEP 562）----
# 新代码请直接使用 get_*() 工厂；以下仅兼容 `from agent.rag_pipeline import retriever`
# 等历史写法，访问时触发对应工厂。
_LAZY_ATTRS = {
    "embeddings": get_embeddings,
    "reranker": get_reranker,
    "llm": get_expansion_llm,
    "retriever": get_retriever,
}


def __getattr__(name: str) -> Any:
    factory = _LAZY_ATTRS.get(name)
    if factory is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return factory()
