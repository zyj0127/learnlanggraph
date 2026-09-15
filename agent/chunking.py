# -*- coding: utf-8 -*-
"""知识库切分：结构感知分层切分 + Markdown 表格结构化提取。

设计目标（agent/rag_pipeline.py 与 eval/weight_sweep.py 共用同一套逻辑，
保证线上检索与离线扫参的 chunk 布局严格一致）：

1. 第一层按 Markdown 标题层级（## / ###）切分，一个小节 = 一个完整语义单元；
2. **表格结构化提取**：小节内的 Markdown 表格在保留原小节的同时，解析成
   「一行一条记录」的**增量**独立切片——每条记录自带表头语义
   （`表头名：单元格值`），并保留 KV 元数据（table_kv）与行号（table_row），
   让引用可以精确到「表格第 N 行」；小节 chunk 提供完整上下文保底召回，
   行切片提供行级精排，双通道互补；
3. 第二层：超过 500 字符的小节再用 RecursiveCharacterTextSplitter(500/50)
   兜底，降低条件句被切断的概率。
"""
import json
import re

from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

_HEADERS_TO_SPLIT_ON = [("##", "Chapter"), ("###", "Section")]
_REC_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=500, chunk_overlap=50, separators=["\n\n", "\n"]
)
# 分隔行（| :--- | :---: | ...）的合法单元格形态
_SEP_CELL_RE = re.compile(r":?-{3,}:?")


def _clean(cell: str) -> str:
    """去掉加粗标记等行内格式，保留文字原样。"""
    return cell.replace("**", "").strip()


def _parse_table_block(lines: list[str]) -> tuple[list[str], list[list[str]]] | None:
    """把一个表格行块解析为（表头, 数据行）；不是规整表格返回 None。"""
    cells = [
        [_clean(c) for c in ln.strip().strip("|").split("|")]
        for ln in lines
    ]
    if len(cells) < 3 or len({len(r) for r in cells}) != 1:
        return None
    header, sep, data = cells[0], cells[1], cells[2:]
    if not all(_SEP_CELL_RE.fullmatch(c) for c in sep):
        return None
    return header, data


def explode_markdown_tables(md_splits: list[Document]) -> list[Document]:
    """把各小节中的 Markdown 表格展开为逐行记录切片（增量式，不改写原小节）。

    - 每行产出一条 Document：正文为「【小节 · 表格第 N 行 / 共 M 行】表头1：值1；…」，
      metadata 继承小节的 Chapter/Section，并追加 table_row / table_kv；
    - 原小节 chunk **原样保留**：小节正文自带「职级/城市/出差」等上下文 prose，
      是这些题在 BM25 侧排进 Top-3 的主力，拆走会造成排名回归（已实测验证）；
      行记录作为行级精排与精确引用的增量通道叠加在其上；
    - 无法解析的伪表格（如正文里的竖线装饰）保持原样不动。
    """
    out: list[Document] = []
    for doc in md_splits:
        lines = doc.page_content.split("\n")
        # 定位连续的表格行块（首尾均为 | 的连续行）
        blocks: list[tuple[int, int]] = []
        i = 0
        while i < len(lines):
            if lines[i].lstrip().startswith("|"):
                j = i
                while j < len(lines) and lines[j].lstrip().startswith("|"):
                    j += 1
                blocks.append((i, j))
                i = j
            else:
                i += 1

        section = doc.metadata.get("Section", "")
        for start, end in blocks:
            parsed = _parse_table_block(lines[start:end])
            if parsed is None:
                continue
            header, data = parsed
            m = len(data)
            for n, row in enumerate(data, 1):
                kv = {h: v for h, v in zip(header, row)}
                record = (
                    f"【{section} · 表格第 {n} 行 / 共 {m} 行】"
                    + "；".join(f"{h}：{v}" for h, v in zip(header, row))
                )
                meta = dict(doc.metadata)
                meta["table_row"] = n
                # 向量库元数据只接受标量值，KV 以 JSON 字符串落库（正文本身已含全部 KV）
                meta["table_kv"] = json.dumps(kv, ensure_ascii=False)
                out.append(Document(page_content=record, metadata=meta))

        out.append(doc)
    return out


def split_handbook(markdown_text: str) -> list[Document]:
    """手册正文 → 最终切片：标题层切分 → 表格结构化 → 字符级兜底。"""
    md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=_HEADERS_TO_SPLIT_ON)
    md_header_splits = md_splitter.split_text(markdown_text)

    structured = explode_markdown_tables(md_header_splits)

    return _REC_SPLITTER.split_documents(structured)
