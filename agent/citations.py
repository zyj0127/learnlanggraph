# -*- coding: utf-8 -*-
"""回答引用溯源：来源编号、检索文本组装与来源解析（零第三方依赖纯函数）。

职责切分：
- number_sources(docs)：给当次检索的 Top-N 片段分配稳定编号 [1][2]...
  （编号 = 重排后名次，当次调用内唯一），产出结构化来源列表；
- format_sources_text(sources)：组装给 LLM 的检索文本（人类可读格式保持
  「来源 [n]: 章 > 节 + 原文」，与既有口径兼容，评测不退化）；
- parse_sources_from_text(text)：从检索文本解析回结构化来源——
  session_runner 在 search_hr_policy 的 ToolMessage 上调用它产出
  sources 事件（不依赖 contextvars 跨 langchain 调用边界传播，
  文本即唯一事实源，解析与组装互逆）。
"""
import re

# 透出给前端的片段摘要长度（完整原文仍随 tool_result 走，此处只是引用卡片）
SNIPPET_LEN = 160

# 来源头部：`来源 [1]: 章 > 节`（与旧格式 `来源 1: 章 > 节` 解析兼容，
# 见 format_sources_text docstring）
_SOURCE_HEADER_RE = re.compile(r"^来源 \[(\d+)\]: (.*?) > (.*?)\s*$", re.M)


def number_sources(docs: list) -> list[dict]:
    """给重排后的文档片段分配稳定编号，返回结构化来源列表。

    每个来源：id（1 起，当次调用内唯一）/ chapter / section / snippet / content。
    """
    sources = []
    for i, doc in enumerate(docs, 1):
        meta = getattr(doc, "metadata", {}) or {}
        content = (getattr(doc, "page_content", "") or "").strip()
        sources.append({
            "id": i,
            "chapter": meta.get("Chapter", "未知章节"),
            "section": meta.get("Section", "未知段落"),
            "snippet": content[:SNIPPET_LEN],
            "content": content,
        })
    return sources


def format_sources_text(sources: list[dict]) -> str:
    """组装检索结果文本（LLM 消费）：编号与正文引用标记 [n] 对应。

    头部格式 `来源 [1]: 章 > 节` 与旧格式 `来源 1: 章 > 节` 解析兼容
    （evaluate._pipeline_rank 按「来源 」切块、telemetry.extract_retrieved
    按 startswith('来源 ')+split(':') 解析，两者均不受影响，评测口径不退化）。
    """
    parts = [
        f"来源 [{s['id']}]: {s['chapter']} > {s['section']}\n{s['content']}"
        for s in sources
    ]
    return "「知识库检索结果」\n" + "\n\n".join(parts)


def parse_sources_from_text(text: str) -> list[dict]:
    """从检索结果文本解析结构化来源（组装的逆运算）。

    非检索结果文本（如「未检索到」兜底或工具拒答文案）返回空列表。
    """
    if not text or "「知识库检索结果」" not in text:
        return []
    matches = list(_SOURCE_HEADER_RE.finditer(text))
    sources = []
    for idx, m in enumerate(matches):
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        snippet = text[start:end].strip()[:SNIPPET_LEN]
        sources.append({
            "id": int(m.group(1)),
            "chapter": m.group(2).strip(),
            "section": m.group(3).strip(),
            "snippet": snippet,
        })
    return sources
