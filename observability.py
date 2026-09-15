# -*- coding: utf-8 -*-
"""本地可观测性：LLM 调用 token 用量、延迟、成本统计。

不依赖外部服务（Langfuse/LangSmith 需账号与密钥），通过 LangChain 原生 callback
在本地采集，用于回答「平均响应延迟 / token 成本」这类面试高频问题。

用法：
    from observability import UsageTracker
    tracker = UsageTracker()
    hr_agent_app.invoke(state, {"callbacks": [tracker], "configurable": {...}})
    print(tracker.summary())
"""
import os
import time
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from logging_config import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent

# DeepSeek 定价（元/百万 token，估算值，可用环境变量覆盖）
PRICE_INPUT_PER_M = float(os.getenv("DEEPSEEK_PRICE_INPUT", "2.0"))   # 输入 ¥/M
PRICE_OUTPUT_PER_M = float(os.getenv("DEEPSEEK_PRICE_OUTPUT", "8.0"))  # 输出 ¥/M


class UsageTracker(BaseCallbackHandler):
    """采集每次 LLM 调用的 token 用量与延迟。"""

    def __init__(self):
        self.llm_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_llm_latency = 0.0
        self.calls = []
        self._start_ts = {}

    # -- callback 钩子 --
    def on_llm_start(self, serialized, prompts, **kwargs):
        run_id = kwargs.get("run_id")
        if run_id:
            self._start_ts[run_id] = time.perf_counter()

    def on_llm_end(self, response: LLMResult, **kwargs):
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
    def _extract_usage(response: LLMResult):
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
        """估算成本（元）。"""
        return (self.total_input_tokens / 1_000_000 * PRICE_INPUT_PER_M
                + self.total_output_tokens / 1_000_000 * PRICE_OUTPUT_PER_M)

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


# ---------------------------------------------------------------------------
# 审计指标：幻觉治理效果的可度量口径
#   checked          本进程内进入审计的轮次数（分母）
#   passed           审计通过数
#   rule_blocked     规则层拦截数（数字类幻觉，零 token 成本）
#   llm_blocked      模型层拦截数（语义类幻觉）
#   parse_errors     审计输出解析失败次数（fail-safe 触发，不放行）
#   fallback_handoff 熔断后转人工兜底次数
#   block_reasons    拦截原因样本（用于人工复核与误杀分析）
# ---------------------------------------------------------------------------
class AuditCounters:
    """审计节点运行指标，供评测脚本与周报计算拦截率/误杀率。"""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.checked = 0
        self.passed = 0
        self.rule_blocked = 0
        self.llm_blocked = 0
        self.parse_errors = 0
        self.fallback_handoff = 0
        self.block_reasons: list = []

    def record_block(self, layer: str, reason: str) -> None:
        if layer == "rule":
            self.rule_blocked += 1
        else:
            self.llm_blocked += 1
        if len(self.block_reasons) < 50:
            self.block_reasons.append({"layer": layer, "reason": reason})

    def snapshot(self) -> dict:
        blocked = self.rule_blocked + self.llm_blocked
        return {
            "checked": self.checked,
            "passed": self.passed,
            "blocked": blocked,
            "rule_blocked": self.rule_blocked,
            "llm_blocked": self.llm_blocked,
            "parse_errors": self.parse_errors,
            "fallback_handoff": self.fallback_handoff,
            "block_rate": round(blocked / self.checked, 4) if self.checked else 0.0,
            "block_reasons": list(self.block_reasons),
        }


# 进程级单例：evaluate.py 与 benchmark.py 可直接 import 读取
AUDIT_COUNTERS = AuditCounters()
