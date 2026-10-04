# -*- coding: utf-8 -*-
"""流式幻觉预检：规则层前置到 token 流出途中（防幻觉体系的最后一块拼图）。

动机
----
事后审计（fact_check_node）在答案**生成完之后**才校验，而 SSE 逐 token
流出意味着幻觉内容在审计前已经上到用户屏幕。本模块把规则层
（fact_rules.check_numbers，同步纯函数、开销可忽略）前置到流式途中：
发现数字类幻觉立即中止流出，改发兜底话术。

切分策略（不误伤的关键）
------------------------
预检输入是**部分文本**，「50」流到「5」时绝不能误判。策略：
1. 只在**句边界**（。！？\\n）触发预检——句边界前的数字必然完整
   （流式 token 从不断句中截出半个数字的情况被天然排除）；
2. 每次预检的是「开头到最近句边界」的前缀（而非增量片段），
   保证规则层看到的上下文与事后审计一致；
3. 片段不含数字直接跳过（正则 extract_facts 前先过一道便宜判定）；
4. 流结束时对剩余缓冲做一次 flush 终检（此时答案已完整，无切半风险）。

两层并存与计数口径
------------------
- 流式预检是**前置防线**，事后审计（fact_check_node）保留不动；
- 预检命中：record_block("rule")（与事后规则层同口径，reason 带「流式」前缀），
  并以 question 为键登记 stream_blocked——事后审计规则层命中同一轮时跳过计数，
  **同一轮同一幻觉不双计**（行为不变：事后审计仍照常打回/熔断，只是计数去重）；
- 开关：Settings.stream_fact_check_enabled=False 时全旁路（feed/flush 恒 None）。
"""
import threading
import time
from typing import Optional

from agent.fact_rules import check_numbers
from logging_config import get_logger

logger = get_logger(__name__)

# 句边界字符：预检只在这些字符之后触发（数字跨 token 切半的场景由此规避）
SENTENCE_ENDINGS = ("。", "！", "？", "\n")


class StreamFactGuard:
    """一轮回答的流式预检器（session_runner 每个 turn 创建一个实例）。"""

    def __init__(self, question: str, enabled: bool = True) -> None:
        self.question = question or ""
        self.enabled = enabled
        self.rag_context = ""
        self.buffer = ""
        self.checked_upto = 0
        self.aborted = False
        self.abort_reason = ""

    def set_context(self, rag_text: str) -> None:
        """记录本轮检索原文（search_hr_policy 的 ToolMessage 内容）。"""
        self.rag_context = rag_text or ""

    def feed(self, token: str) -> Optional[str]:
        """喂入一个 chatbot token；命中幻觉返回违规原因（调用方立即中止流出）。

        返回 None 表示继续放行。一旦 aborted，后续 feed 恒返回已中止原因。
        """
        if self.aborted:
            return self.abort_reason
        self.buffer += token or ""
        return self._check_at_boundary()

    def flush(self) -> Optional[str]:
        """流结束终检：检查句边界之后的剩余缓冲（答案已完整，无切半风险）。"""
        if self.aborted:
            return self.abort_reason
        return self._run_check(self.buffer, final=True)

    # ---- 内部 ----
    def _check_at_boundary(self) -> Optional[str]:
        if not self.enabled or not self.rag_context:
            self.checked_upto = len(self.buffer)
            return None
        # 最近一个句边界位置（在已检位置之后）
        last = -1
        for end in SENTENCE_ENDINGS:
            idx = self.buffer.rfind(end, self.checked_upto)
            last = max(last, idx)
        if last < 0:
            return None  # 尚无新完整句子
        prefix = self.buffer[:last + 1]
        self.checked_upto = last + 1
        if not any(ch.isdigit() for ch in prefix):
            return None  # 无数字不触发规则层（便宜判定）
        return self._run_check(prefix, final=False)

    def _run_check(self, text: str, final: bool) -> Optional[str]:
        if not self.enabled or not self.rag_context:
            return None
        violations = check_numbers(text, self.rag_context, self.question)
        if not violations:
            return None
        self.aborted = True
        self.abort_reason = "；".join(violations)
        mark_stream_blocked(self.question)
        logger.warning("流式预检拦截数字幻觉（%s）：%s",
                       "终检" if final else "句边界", self.abort_reason)
        return self.abort_reason


# ---- 流式拦截登记（事后审计计数去重用）----
# question → 拦截时间戳；事后审计规则层命中同一问题时跳过计数（同轮不双计）。
_BLOCKED: dict = {}
_BLOCKED_LOCK = threading.Lock()
_BLOCKED_TTL_S = 600  # 10 分钟内的同一问题视为同一轮（打回重写通常秒级）


def mark_stream_blocked(question: str) -> None:
    with _BLOCKED_LOCK:
        _BLOCKED[question or ""] = time.time()
        # 简单清理过期项，防长跑进程膨胀
        if len(_BLOCKED) > 1000:
            now = time.time()
            for key in [k for k, ts in _BLOCKED.items() if now - ts > _BLOCKED_TTL_S]:
                _BLOCKED.pop(key, None)


def was_stream_blocked(question: str) -> bool:
    """该问题的本轮回答是否已被流式预检拦截（事后审计计数去重判定）。"""
    with _BLOCKED_LOCK:
        ts = _BLOCKED.get(question or "")
        if ts is None:
            return False
        if time.time() - ts > _BLOCKED_TTL_S:
            _BLOCKED.pop(question or "", None)
            return False
        return True
