# -*- coding: utf-8 -*-
"""审计规则层：用确定性规则拦截「数字类幻觉」。

为什么要有这一层
----------------
金额、天数、百分比、职级门槛这类事实，幻觉的典型表现是「回答里的数字在知识库原文中
并不存在」。用正则抽取 + 集合比对来判定，比让模型判断更准、更快、零 token 成本，
而且可解释、可写单元测试。语义层面的偏差（条件被吃掉、原文没说但被暗示）仍交给
模型层处理，两层互补。

误杀控制（重要）
----------------
1. 用户提问里出现过的数字视为白名单，避免把「员工问 5 天，回答复述 5 天」判成幻觉；
2. 只比对「带单位的数字」与职级编号，条款编号（如 2.5、1.1）不参与比对；
3. 规则层是单向的：**命中即拦截，未命中不代表通过**，仍需模型层复核。

用法
----
    from agent.fact_rules import check_numbers
    violations = check_numbers(answer, rag_context, question)
    if violations:  # 直接判不通过，不必再调模型
        ...
"""
import re
from typing import List, Tuple

# 需要严格比对的事实模式：(类型, 正则)
# 注意：只覆盖「带明确单位」的数值与职级编号，避免误伤条款号、日期、年份
#   - 月数必须写成「N 个月」，否则会把「2026 年 1 月 1 日」这类日期判成幻觉；
#   - 年限（N 年）刻意不纳入：中文提问常写「两年」「四年」，数字形态不一致，
#     纳入后极易把正确回答误判为幻觉，收益不抵风险。
FACT_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("金额", re.compile(r"(\d+(?:\.\d+)?)\s*(?:万元|万|千元|元|块钱)")),
    ("天数", re.compile(r"(\d+(?:\.\d+)?)\s*(?:个)?天")),
    ("月数", re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*个月")),
    ("小时", re.compile(r"(\d+(?:\.\d+)?)\s*小时")),
    ("百分比", re.compile(r"(\d+(?:\.\d+)?)\s*%")),
    ("倍数", re.compile(r"(\d+(?:\.\d+)?)\s*倍")),
    ("次数", re.compile(r"(\d+(?:\.\d+)?)\s*次")),
    ("职级", re.compile(r"[Pp](\d{1,2})(?![\d.])")),
)


def _normalize(value: str) -> str:
    """数值归一化：5.0 -> 5，避免因写法差异产生误判。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return value
    return str(int(num)) if num == int(num) else str(num)


def extract_facts(text: str) -> List[Tuple[str, str]]:
    """抽取文本中的「事实型数值」，返回 [(类型, 归一化值), ...]。"""
    facts: List[Tuple[str, str]] = []
    for kind, pattern in FACT_PATTERNS:
        for match in pattern.finditer(text or ""):
            facts.append((kind, _normalize(match.group(1))))
    return facts


def check_numbers(answer: str, context: str, question: str = "") -> List[str]:
    """比对回答与知识库原文的数值事实，返回不一致项的描述列表。

    :param answer:   模型生成的回答
    :param context:  本轮 RAG 工具返回的知识库原文
    :param question: 用户原始问题（其中的数字视为合法白名单）
    :return:         违规描述列表；为空表示规则层未发现数字类幻觉
    """
    if not context:
        return []

    allowed = {value for _, value in extract_facts(context)}
    allowed |= {value for _, value in extract_facts(question)}

    violations: List[str] = []
    seen = set()
    for kind, value in extract_facts(answer):
        if value in allowed or value in seen:
            continue
        seen.add(value)
        violations.append(f"{kind} {value} 未在知识库原文中出现")
    return violations
