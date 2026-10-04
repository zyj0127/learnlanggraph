# -*- coding: utf-8 -*-
"""语义缓存：政策类问答的答案缓存（embedding 余弦相似度命中）。

为什么
------
政策问答占流量大头且答案公共（与 uid 无关）：「年假几天」「住宿报销标准」
被不同员工反复提问，每次都走完整 LLM + 检索 + 审计链路。本模块把
**审计通过的最终答案**按问题语义缓存起来，相似问题直接命中返回，
省掉整轮 LLM 成本（命中时 token 消耗为 0）。

设计
----
- key = 问题 embedding（复用 rag_pipeline.get_embeddings，本地 BGE 零成本），
  余弦相似度 ≥ 阈值（Settings.semantic_cache_threshold，默认 0.92）即命中；
  缓存规模小（百级条目），全量扫描足够，不上 ANN 索引；
- 存储 db/semantic_cache.db（SQLite，自愈建表，对齐 telemetry 模式），
  TTL（默认 7 天，政策会修订）+ 命中计数；
- 别名：写入时若本轮 query 经追问改写（query != 用户原问），原问与改写后
  查询**各写一行**共享答案——改写后的相同追问也能命中；
- 安全边界：只缓存「纯政策问答」——本轮工具调用 ⊆ {search_hr_policy}、
  无审批挂起、最终答案非转人工/熔断话术。含个人数据（档案/余额）或写操作
  （证明/请假）的答案**绝不缓存**；缓存内容本身不含 uid；
- 审计不被旁路：命中返回的答案本身就是审计通过的产物；
- 开关：Settings.semantic_cache_enabled=False 时 lookup/store 全旁路。

本模块 import 轻量：不加载模型、不创建 LLM；embedding 在首次 lookup/store
时经 rag_pipeline.get_embeddings 懒加载（可注入 embed_fn 供测试）。
"""
import json
import math
import sqlite3
import struct
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, List, Optional

from config import get_settings
from logging_config import get_logger

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question    TEXT    NOT NULL,        -- 写入时的问题（原问或改写后查询，即别名行）
    answer      TEXT    NOT NULL,        -- 审计通过的最终答案
    sources     TEXT,                    -- 引用来源 JSON 数组（additive，可为空）
    embedding   BLOB    NOT NULL,        -- float32 小端字节序
    created_at  TEXT    NOT NULL,        -- ISO 时间戳
    hits        INTEGER DEFAULT 0        -- 命中计数
);
CREATE INDEX IF NOT EXISTS idx_cache_created ON cache_entries(created_at);

-- 全局计数器（lookups / hits）：周报命中率口径真源
CREATE TABLE IF NOT EXISTS cache_counters (
    key    TEXT PRIMARY KEY,
    value  INTEGER DEFAULT 0
);
"""


def _db_path() -> str:
    return str(get_settings().semantic_cache_db)


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _db(db_path: Optional[str] = None):
    """连接上下文：提交并**显式关闭**（Windows 下句柄不释放会锁库文件）。

    用法：`with _db() as conn: conn.execute(...)`。
    """
    return closing(_connect(db_path))


def init_db(db_path: Optional[str] = None) -> None:
    with _db(db_path) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


# ---- embedding ----
def _embed(texts: List[str], embed_fn: Optional[Callable] = None) -> List[List[float]]:
    """文本 → 向量。embed_fn 可注入（测试）；None 时懒加载 BGE。"""
    if embed_fn is not None:
        return [embed_fn(t) for t in texts]
    from agent.rag_pipeline import get_embeddings

    embeddings = get_embeddings()
    return [embeddings.embed_query(t) for t in texts]


def _pack(vec: List[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack(blob: bytes) -> List[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """余弦相似度（纯函数，可单测）。零向量返回 0。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _bump(conn: sqlite3.Connection, key: str) -> None:
    conn.execute(
        "INSERT INTO cache_counters (key, value) VALUES (?, 1)"
        " ON CONFLICT(key) DO UPDATE SET value = value + 1",
        (key,))


def _purge_expired(conn: sqlite3.Connection) -> None:
    ttl = get_settings().semantic_cache_ttl_days
    cutoff = (datetime.now() - timedelta(days=ttl)).isoformat(timespec="seconds")
    cur = conn.execute("DELETE FROM cache_entries WHERE created_at < ?", (cutoff,))
    if cur.rowcount:
        logger.info("语义缓存过期清理：%d 条（TTL %d 天）", cur.rowcount, ttl)


# ---- 对外 API ----
def cache_lookup(question: str, *, embed_fn: Optional[Callable] = None,
                 db_path: Optional[str] = None) -> Optional[dict]:
    """按语义查找缓存答案；命中返回 {answer, sources, similarity}，未命中 None。

    任何异常（模型未加载/库损坏）都静默降级为未命中——缓存是优化不是依赖。
    """
    settings = get_settings()
    if not settings.semantic_cache_enabled:
        return None
    question = (question or "").strip()
    if not question:
        return None
    try:
        init_db(db_path)
        with _db(db_path) as conn:
            _purge_expired(conn)
            _bump(conn, "lookups")
            rows = conn.execute(
                "SELECT id, answer, sources, embedding FROM cache_entries").fetchall()

            result = None
            if rows:
                qvec = _embed([question], embed_fn)[0]
                best, best_sim = None, -1.0
                for row in rows:
                    sim = cosine_similarity(qvec, _unpack(row[3]))
                    if sim > best_sim:
                        best, best_sim = row, sim
                if best is not None and best_sim >= settings.semantic_cache_threshold:
                    _bump(conn, "hits")
                    conn.execute(
                        "UPDATE cache_entries SET hits = hits + 1 WHERE id = ?",
                        (best[0],))
                    result = {
                        "answer": best[1],
                        "sources": json.loads(best[2]) if best[2] else [],
                        "similarity": round(best_sim, 4),
                    }
                else:
                    logger.info("语义缓存未命中（最高相似度 %.3f < %.2f）",
                                max(best_sim, 0.0), settings.semantic_cache_threshold)
            conn.commit()
        if result is not None:
            logger.info("语义缓存命中（相似度 %.4f ≥ %.2f）",
                        result["similarity"], settings.semantic_cache_threshold)
        return result
    except Exception as e:  # 缓存故障不得影响主链路
        logger.warning("语义缓存查询失败（按未命中处理）：%s", e)
        return None


def cache_store(question: str, answer: str, *,
                sources: Optional[list] = None,
                alias: Optional[str] = None,
                embed_fn: Optional[Callable] = None,
                db_path: Optional[str] = None) -> bool:
    """写入缓存。question 为用户原问；alias 为改写后的检索查询（不同则各写一行）。

    返回是否写入成功；异常静默返回 False。
    """
    settings = get_settings()
    if not settings.semantic_cache_enabled:
        return False
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not question or not answer:
        return False
    try:
        init_db(db_path)
        texts = [question]
        alias = (alias or "").strip()
        if alias and alias != question:
            texts.append(alias)
        vecs = _embed(texts, embed_fn)
        now = datetime.now().isoformat(timespec="seconds")
        sources_json = json.dumps(sources, ensure_ascii=False) if sources else None
        with _db(db_path) as conn:
            for text, vec in zip(texts, vecs):
                conn.execute(
                    "INSERT INTO cache_entries (question, answer, sources, embedding,"
                    " created_at, hits) VALUES (?,?,?,?,?,0)",
                    (text, answer, sources_json, _pack(vec), now))
            conn.commit()
        logger.info("语义缓存写入 %d 条（问题 %d 字，答案 %d 字）",
                    len(texts), len(question), len(answer))
        return True
    except Exception as e:
        logger.warning("语义缓存写入失败：%s", e)
        return False


def cache_stats(days: int = 7, *, db_path: Optional[str] = None) -> dict:
    """缓存统计（周报用）：命中率 + 条目数 + 总命中次数。

    计数器是累计值，不区分周期（条目有 TTL，累计口径足够回答「省了多少」）；
    表不存在时返回全零结构，不报错。
    """
    empty = {"enabled": get_settings().semantic_cache_enabled,
             "lookups": 0, "hits": 0, "hit_rate": 0.0, "entries": 0,
             "threshold": get_settings().semantic_cache_threshold}
    try:
        init_db(db_path)
        with _db(db_path) as conn:
            counters = dict(conn.execute("SELECT key, value FROM cache_counters"))
            entries = conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
        lookups = int(counters.get("lookups", 0))
        hits = int(counters.get("hits", 0))
        return {**empty, "lookups": lookups, "hits": hits, "entries": entries,
                "hit_rate": round(hits / lookups, 4) if lookups else 0.0}
    except Exception as e:
        logger.warning("语义缓存统计失败：%s", e)
        return empty
