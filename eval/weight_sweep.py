# -*- coding: utf-8 -*-
"""混合召回权重扫描：在真实评测集上对比不同 BM25 / 向量配比的 Hit@3、Hit@5。

用途：给「0.4/0.6 是怎么定的」提供可复现的数据依据。
不改动项目任何现有文件，只读取 data/company_handbook.md 与 eval/dataset.py。

放置位置：E:\\code\\py\\learnlanggraph\\eval\\weight_sweep.py
运行方式（项目根目录下）：
    /e/File/virtualenv/test_py02/Scripts/python.exe -X utf8 eval/weight_sweep.py

输出：eval/weight_sweep_result.json（含逐题明细，未命中的题可直接当 badcase 清单）
"""
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")

from langchain_chroma import Chroma  # noqa: E402
from langchain_classic.retrievers import EnsembleRetriever  # noqa: E402
from langchain_community.retrievers import BM25Retriever  # noqa: E402
from langchain_huggingface import HuggingFaceEmbeddings  # noqa: E402
from langchain_text_splitters import (  # noqa: E402
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from config import DOC_PATH  # noqa: E402
from eval.dataset import POLICY_EVAL_SET  # noqa: E402

OUT = Path(__file__).resolve().parent / "weight_sweep_result.json"

# ---- 与 agent/rag_pipeline.build_ensemble_retriever 保持一致的切分配置 ----
TEXT = DOC_PATH.read_text(encoding="utf-8")
print(f"[info] 知识库文件 {DOC_PATH.name}，{len(TEXT)} 字符")

embeddings = HuggingFaceEmbeddings(
    model_name=os.getenv("EMBEDDING_MODEL"),
    model_kwargs={"device": "cpu"},
    encode_kwargs={"normalize_embeddings": True},
)

md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=[("##", "Chapter"), ("###", "Section")])
md_splits = md_splitter.split_text(TEXT)
rec_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50, separators=["\n\n", "\n"])
splits = rec_splitter.split_documents(md_splits)
print(f"[info] chunk 总数：{len(splits)}  （markdown 层切分后 {len(md_splits)} 段）")

bm25 = BM25Retriever.from_documents(splits)
bm25.k = 5
vectorstore = Chroma.from_documents(documents=splits, embedding=embeddings)
vector_retriever = vectorstore.as_retriever(search_kwargs={"k": 5})

# (向量权重, BM25 权重, 标签)
CONFIGS = [
    (0.1, 0.9, "向量0.1 / BM25 0.9"),
    (0.15, 0.85, "向量0.15 / BM25 0.85"),
    (0.4, 0.6, "向量0.4 / BM25 0.6（旧默认，扩库后失效）"),
    (0.5, 0.5, "向量0.5 / BM25 0.5"),
    (0.6, 0.4, "向量0.6 / BM25 0.4"),
    (0.85, 0.15, "向量0.85 / BM25 0.15"),
    (1.0, 0.0, "纯向量"),
    (0.0, 1.0, "纯 BM25"),
]

results, detail_rows = {}, {}
for vw, bw, label in CONFIGS:
    ens = EnsembleRetriever(retrievers=[vector_retriever, bm25], weights=[vw, bw])
    hit3 = hit5 = 0
    rows = []
    for item in POLICY_EVAL_SET:
        q, src = item["question"], item["expected_source"]
        contents = [d.page_content or "" for d in ens.invoke(q)]
        h3 = any(src in c for c in contents[:3])
        h5 = any(src in c for c in contents[:5])
        hit3 += int(h3)
        hit5 += int(h5)
        rows.append({"question": q, "hit3": h3, "hit5": h5, "n_docs": len(contents)})

    total = len(POLICY_EVAL_SET)
    results[label] = {
        "hit3": hit3, "total": total, "hit3_rate": round(hit3 / total, 4),
        "hit5": hit5, "hit5_rate": round(hit5 / total, 4),
    }
    detail_rows[label] = rows
    print(f"  {label:36s} Hit@3 = {hit3}/{total} = {hit3 / total:.1%}   Hit@5 = {hit5}/{total} = {hit5 / total:.1%}")

OUT.write_text(
    json.dumps(
        {"chunks": len(splits), "md_sections": len(md_splits), "results": results, "detail": detail_rows},
        ensure_ascii=False,
        indent=2,
    ),
    encoding="utf-8",
)
print(f"\n[done] {OUT}")

cur_label = "向量0.6 / BM25 0.4"
miss = [r["question"] for r in detail_rows[cur_label] if not r["hit3"]]
print(f"\n[current config] Top-3 未命中 {len(miss)} 题：")
for m in miss:
    print("  ×", m)
