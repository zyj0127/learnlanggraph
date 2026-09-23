# -*- coding: utf-8 -*-
"""LLM 调用用量采集：token 用量、延迟、成本（LangChain callback 实现）。

不依赖外部服务（Langfuse/LangSmith 需账号与密钥），通过 LangChain 原生 callback
在本地采集，用于回答「平均响应延迟 / token 成本」这类面试高频问题。

用法：
    from observability import UsageTracker
    tracker = UsageTracker()
    hr_agent_app.invoke(state, {"callbacks": [tracker], "configurable": {...}})
    print(tracker.summary())
"""
import time
from typing import Any, Dict, List

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from config import get_settings


class UsageTracker(BaseCallbackHandler):
    """采集每次 LLM 调用的 token 用量与延迟。"""

    def __init__(self) -> None:
        self.llm_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_llm_latency = 0.0
        self.calls: List[Dict[str, Any]] = []
        self._start_ts: Dict[Any, float] = {}

    # -- callback 钩子 --
    def on_llm_start(self, serialized, prompts, **kwargs) -> None:
        run_id = kwargs.get("run_id")
        if run_id:
            self._start_ts[run_id] = time.perf_counter()

    def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        run_id = kwargs.get("run_id")
        start = self._start_ts.pop(run_id, None)
        latency = (time.perf_counter() - start) if start else 0.0

        in_tokens, out_tokens = self._extract_usage(response)
        self.llm_calls += 1
        self.total_input_tokens += in_tokens
        self.total_output_tokens += out_tokens
        self.total_llm_latency += latency
        self.calls.append({
            "latency_s": round(latency, 3),
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
        })

    # -- 工具方法 --
    @staticmethod
    def _extract_usage(response: LLMResult) -> tuple:
        """从 LLMResult 提取 token 用量（优先 message.usage_metadata）。"""
        in_tokens = out_tokens = 0
        try:
            for gen_list in response.generations:
                for gen in gen_list:
                    msg = getattr(gen, "message", None)
                    um = getattr(msg, "usage_metadata", None) or {}
                    in_tokens += um.get("input_tokens", 0)
                    out_tokens += um.get("output_tokens", 0)
        except Exception:
            pass
        if in_tokens == 0 and out_tokens == 0:
            tok = (response.llm_output or {}).get("token_usage") or {}
            in_tokens = tok.get("prompt_tokens", 0)
            out_tokens = tok.get("completion_tokens", 0)
        return in_tokens, out_tokens

    def cost(self) -> float:
        """估算成本（元）。定价取自统一 Settings（可用环境变量覆盖）。"""
        settings = get_settings()
        return (self.total_input_tokens / 1_000_000 * settings.deepseek_price_input
                + self.total_output_tokens / 1_000_000 * settings.deepseek_price_output)

    def summary(self) -> dict:
        """汇总统计。"""
        return {
            "llm_calls": self.llm_calls,
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "llm_latency_s": round(self.total_llm_latency, 3),
            "avg_llm_latency_s": round(self.total_llm_latency / self.llm_calls, 3) if self.llm_calls else 0,
            "estimated_cost_rmb": round(self.cost(), 4),
        }
