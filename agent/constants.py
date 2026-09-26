# -*- coding: utf-8 -*-
"""前后端共享的协议常量与策略表。

前端（streamlit_app.py）与后端（agent/nodes.py、agent/routers.py）
统一从这里引用，避免魔法字符串各处复制后漂移。
"""

# 后端隐藏指令：应用层注入，触发闲置会话总结
IDLE_TIMEOUT_CMD = "__SYS_IDLE_TIMEOUT__"
# 审计打回时注入的系统消息前缀（前端渲染时需过滤）
AUDIT_FAIL_PREFIX = "SYSTEM AUTO FAILED"
# 转人工消息前缀：路由据此跳过审计直接结束
HANDOFF_PREFIX = "【转人工】"
# 闲置会话总结的消息前缀
SUMMARY_PREFIX = "【会话闲置总结】"

# 需要人工审批的敏感工具集合（新增敏感工具时在此登记即可）
SENSITIVE_TOOLS = {"generate_employment_certification", "apply_leave"}

# 转人工兜底：情绪/投诉关键词（命中即转接真人 HR，避免 LLM 硬答激化情绪）
DISTRESS_KEYWORDS = (
    "投诉", "太过分", "气死", "生气", "愤怒", "不满意", "凭什么", "离谱",
    "忍无可忍", "忍不了", "受够了", "我要找领导", "我要见hr", "我要见人事",
    "敷衍", "态度差", "垃圾", "仲裁", "律师函", "曝光", "威胁", "不负责任",
)

# ---- 审计与反思策略（统一在此定义，避免策略散落在多个模块）----
# 反思重写次数上限：超过即熔断，避免「审计打回 → 重写 → 再打回」的死循环
MAX_REFLECTION_LOOPS = 4
# 审计输出解析失败的容忍次数：超过后不再打回，转为人工兜底
# （fail-safe 原则：宁可转人工，也不放行未经校验的内容）
AUDIT_PARSE_RETRY_LIMIT = 1
# 审计熔断/无法校验时的对外兜底话术（沿用转人工前缀，前端按转人工样式渲染）
AUDIT_FALLBACK_MESSAGE = (
    f"{HANDOFF_PREFIX}抱歉，本次回答未能通过事实校验，为避免向您提供不准确的制度信息，"
    "系统已中止本次自动答复。请稍后重试，或由我为您转接 HR 人工核实。"
)
