# -*- coding: utf-8 -*-
"""TEI（text-embeddings-inference）推理服务客户端封装。

企业化第二阶段：把嵌入/重排从「进程内加载 BGE 权重」解耦为独立推理服务，
应用容器不再背 3.4GB 模型与 torch 运行时，扩缩容与 GPU 迁移只动 TEI 侧。

本模块约定：
- **零重依赖**：只用标准库 urllib，import 轻量（与 rag_pipeline 懒加载约定一致）；
- 仅在 Settings.embedding_backend == "tei" 时被 rag_pipeline 工厂调用；
- TEIReranker 保持与 sentence_transformers.CrossEncoder 相同的调用面：
  `predict(sentence_pairs) -> 与输入等长的 score 序列`，调用点零改动。

TEI 接口口径：
- embedding：OpenAI 兼容 `POST {base}/v1/embeddings`（由 langchain_openai 直连，
  本模块不封装）；
- rerank：`POST {base}/rerank`，请求 {"query": str, "texts": [str, ...]}，
  响应 [{"index": int, "score": float}, ...]（按 score 降序，index 指回 texts）。
"""
import json
import urllib.request
from collections import OrderedDict
from typing import List, Sequence

from logging_config import get_logger

logger = get_logger(__name__)


def _post_json(url: str, payload: dict, timeout: float = 30.0):
    """POST JSON 并解析响应；网络/协议异常原样抛出（检索链路本身有上层降级）。"""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def normalize_tei_base(url: str) -> str:
    """TEI 服务根地址归一化：去掉尾部斜杠（空值原样返回，由调用方校验）。"""
    return (url or "").strip().rstrip("/")


class TEIReranker:
    """CrossEncoder 调用面兼容的 TEI reranker 客户端。

    predict(sentence_pairs) 接收 [[query, doc], ...]（与 CrossEncoder.predict 一致），
    按 query 分组批量调 /rerank（同 query 多 doc 一次请求，减少往返），
    返回与输入顺序一一对应的 float score 列表。
    """

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        base = normalize_tei_base(base_url)
        if not base:
            raise ValueError("TEI_RERANKER_URL 未配置（embedding_backend=tei 时必填）")
        self._endpoint = f"{base}/rerank"
        self._timeout = timeout

    def predict(self, sentence_pairs: Sequence[Sequence[str]]) -> List[float]:
        if not sentence_pairs:
            return []

        # 按 query 分组（保持首次出现顺序），同组 texts 一次 /rerank 请求
        groups: "OrderedDict[str, List[int]]" = OrderedDict()
        for idx, pair in enumerate(sentence_pairs):
            query, doc = pair[0], pair[1]
            groups.setdefault(query, []).append(idx)

        scores: List[float] = [0.0] * len(sentence_pairs)
        for query, indices in groups.items():
            texts = [sentence_pairs[i][1] for i in indices]
            result = _post_json(self._endpoint, {"query": query, "texts": texts},
                                timeout=self._timeout)
            # TEI 响应按 score 降序，用 index 映射回原输入位置
            rank = {int(item["index"]): float(item["score"]) for item in result}
            for local_i, global_i in enumerate(indices):
                if local_i not in rank:
                    raise ValueError(
                        f"TEI /rerank 响应缺少 index={local_i}（endpoint={self._endpoint}）"
                    )
                scores[global_i] = rank[local_i]
        return scores
