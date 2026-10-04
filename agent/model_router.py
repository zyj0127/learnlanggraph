# -*- coding: utf-8 -*-
"""分级模型路由：闲聊/问候走轻量模型，政策/工具/审计走主模型。

动机
----
「你好」「谢谢」这类问候约占真实流量的一两成，用旗舰模型回答纯属浪费。
本模块提供纯函数路由判定：只有**高置信闲聊**（短、无政策主题词、无工具
意图词、命中问候/闲聊模式）才分流到轻量模型；其余一律主模型——
宁可少分流也不错分流（错分流会让政策问题失去检索/工具能力，体验受损）。

- 轻量模型由 Settings.llm_model_light 配置；未配置时 get_light_chat_llm
  回退主模型，路由结果无差别（行为与现状完全一致）；
- 审计节点（fact_check）永远用主模型，质量不可降；
- 路由结果经 UsageTracker 的模型名统计透出到埋点（model_tier），周报可见。
"""
from typing import List

# 问候/闲聊模式（高置信：短句 + 命中即分流）
_GREETING_PATTERNS = (
    "你好", "您好", "在吗", "在么", "谢谢", "感谢", "多谢", "再见", "拜拜",
    "早上好", "晚上好", "下午好", "晚安", "早", "吃了吗", "辛苦",
    "你是谁", "你能做什么", "你能干嘛", "你会什么", "hi", "hello", "hey",
    "ok", "okay", "嗯", "好的", "好哒", "收到",
)

# 政策主题词：命中即主模型（需要检索手册）
_POLICY_WORDS = (
    "年假", "病假", "事假", "婚假", "产假", "育儿假", "调休", "加班",
    "差旅", "报销", "住宿", "餐补", "出差", "补贴", "津贴", "团建",
    "工资", "薪资", "薪酬", "社保", "公积金", "个税", "绩效", "调薪",
    "离职", "入职", "转正", "证明", "工牌", "门禁", "体检", "福利",
    "考勤", "打卡", "居家办公", "晋升", "培训", "报销", "发票",
    "制度", "规定", "政策", "手册", "流程", "标准", "多少", "怎么", "如何",
    "几天", "多少钱", "上限", "额度",
)

# 工具意图词：命中即主模型（需要调 HR 数据/写工具）
_TOOL_WORDS = (
    "请假", "开证明", "开个", "申请", "查询", "查一下", "余额", "档案",
    "工资单", "我的", "帮我查", "帮我开", "帮我申请", "在职证明", "收入证明",
)

# 分流长度上限：超过即主模型（短句才可能是纯闲聊）
_LIGHT_MAX_LEN = 15


def route_model_tier(question: str) -> str:
    """路由判定（纯函数）：返回 "light" / "main"。

    light 需同时满足：
    1. 问题长度 ≤ 15 字；
    2. 不含政策主题词 / 工具意图词；
    3. 命中问候/闲聊模式。
    其余一律 main（保守原则：宁可少分流也不错分流）。
    """
    q = (question or "").strip().lower()
    if not q or len(q) > _LIGHT_MAX_LEN:
        return "main"
    if any(w in q for w in _POLICY_WORDS):
        return "main"
    if any(w in q for w in _TOOL_WORDS):
        return "main"
    if any(p in q for p in _GREETING_PATTERNS):
        return "light"
    return "main"


def light_model_name() -> str:
    """当前轻量模型名（未配置返回空串，调用方按主模型处理）。"""
    from config import get_settings

    return (get_settings().llm_model_light or "").strip()


def usage_tier(models: List[str]) -> str:
    """从本轮 LLM 调用的模型名列表归纳档位（埋点口径，纯函数）。

    - 未配置轻量模型：一律 "main"（路由无差别）；
    - 本轮所有调用都是轻量模型 → "light"；
    - 混合（轻量闲聊 + 主模型审计等）或全主模型 → "main"。
    """
    light = light_model_name()
    if not light:
        return "main"
    names = [m for m in models if m]
    if names and all(m == light for m in names):
        return "light"
    return "main"
