# -*- coding: utf-8 -*-
"""LangGraph 节点实现：执行者 / 人工审批 / 事实审计。

懒加载约定：import 本模块不创建任何 LLM 客户端、不加载模型、不联网；
所有 LLM 实例由 lru_cache 工厂在节点首次执行时创建并缓存。
"""
import uuid
from functools import lru_cache
from typing import List, Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.output_parsers import JsonOutputParser
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from agent.constants import (
    AUDIT_FAIL_PREFIX,
    AUDIT_FALLBACK_MESSAGE,
    AUDIT_PARSE_RETRY_LIMIT,
    DISTRESS_KEYWORDS,
    HANDOFF_PREFIX,
    IDLE_TIMEOUT_CMD,
    MAX_REFLECTION_LOOPS,
    SENSITIVE_TOOLS,
    SUMMARY_PREFIX,
)
from agent.fact_rules import check_numbers
from agent.rag_pipeline import search_hr_policy
from agent.state import AgentState
from config import get_chat_llm
from logging_config import get_logger
from observability import AUDIT_COUNTERS
from tools.hr_tools import (
    generate_employment_certification,
    get_employee_profile,
    get_leave_balance,
)

logger = get_logger(__name__)

# ---- LLM 与工具绑定（懒加载工厂，import 时不创建客户端）----
ALL_TOOLS = [
    get_employee_profile,
    get_leave_balance,
    generate_employment_certification,
    search_hr_policy,
]


@lru_cache(maxsize=1)
def get_executor_llm():
    """执行者 LLM：低温（0.0）保证工具调用与作答的确定性。"""
    return get_chat_llm(temperature=0.0)


@lru_cache(maxsize=1)
def get_llm_with_tools():
    """执行者 LLM + 全量工具绑定（首次节点执行时构建并缓存）。"""
    return get_executor_llm().bind_tools(ALL_TOOLS)


@lru_cache(maxsize=1)
def get_checker_llm():
    """审计者 LLM：独立实例，与执行者解耦。"""
    return get_chat_llm(temperature=0.0)


def detect_handoff_reason(messages: List[BaseMessage]) -> Optional[str]:
    """判断当前轮是否应转人工，返回转接原因；无需转接返回 None。

    仅在「当前轮真实用户输入」范围内检测：
    1. 情绪/投诉关键词命中 → 需人工安抚；
    2. 本轮 RAG 工具明确返回「未检索到」→ 超出知识库范围，需人工解答。
    避免误判历史轮次的内容。
    """
    # 定位最近一条真实用户消息（排除闲置总结与审计打回的隐藏消息）
    last_user_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if isinstance(m, HumanMessage):
            text = m.content or ""
            if text != IDLE_TIMEOUT_CMD and not text.startswith(AUDIT_FAIL_PREFIX):
                last_user_idx = i
                break

    if last_user_idx < 0:
        return None

    user_text = messages[last_user_idx].content or ""
    # 1. 情绪识别
    if any(kw in user_text for kw in DISTRESS_KEYWORDS):
        return "情绪激动或存在投诉倾向，需人工安抚"

    # 2. 超出知识库：只看本轮用户消息之后的 RAG 结果
    for m in messages[last_user_idx + 1:]:
        if getattr(m, "name", "") == "search_hr_policy":
            if "未检索到" in (m.content or ""):
                return "超出公司制度知识库范围，需人工解答"
            break  # 本轮已有有效命中，不再转人工

    return None


def chatbot_node(state: AgentState) -> dict:
    """执行者节点：意图理解、工具调用与内容生成。"""
    messages = state.get("messages", [])

    # 拦截应用层发来的「超时总结」隐藏指令
    last_message = messages[-1]
    if isinstance(last_message, HumanMessage) and last_message.content == IDLE_TIMEOUT_CMD:
        logger.info("超时触发压缩历史会话，生成自动总结")
        summary_llm = get_chat_llm(temperature=0.3)
        summary_prompt = (
            "你是一名hr助手，请用一两句话，总结上面对话中员工咨询的核心问题以及你给出的最终结论"
            f"直接输出结果，并以{SUMMARY_PREFIX}这几个字开头"
        )
        response = summary_llm.invoke(messages[:-1] + [SystemMessage(content=summary_prompt)])
        return {"messages": [response]}

    # 转人工兜底：情绪激动或超出范围时直接转接，避免幻觉或死循环
    handoff_reason = detect_handoff_reason(messages)
    if handoff_reason:
        logger.warning("转人工兜底触发：%s", handoff_reason)
        ticket = f'HR-{state.get("current_uid", "?").upper()}-{uuid.uuid4().hex[:6].upper()}'
        handoff_msg = AIMessage(content=(
            f"{HANDOFF_PREFIX}非常抱歉给您带来困扰。根据我的判断，您本次咨询{handoff_reason}。\n\n"
            f"已为您登记转接真人 HR 专员优先处理，转接编号：{ticket}。\n"
            f"请保持联系方式畅通，专员将尽快通过内部渠道与您取得联系，感谢您的理解。"
        ))
        return {"messages": [handoff_msg]}

    # 首轮对话注入 System Prompt
    if len(messages) == 1:
        system_msg = SystemMessage(
            content=f"你是飞羽科技的高级hr 智能助理。\n"
                    f'当前提问员工的 uid 是：{state.get("current_uid")}。\n'
                    f"回答具体问题前，请务必先调用 get_employee_profile 获取该员工的工作属性，再基于事实作答。\n"
                    f"必须基于工具的返回事实，绝不能编造数字或条件")
        messages = [system_msg] + messages

    response = get_llm_with_tools().invoke(messages)
    return {"messages": [response], "loop_state": state.get("loop_state", 0) + 1}


def human_review_node(state: AgentState) -> dict:
    """人工介入节点：敏感工具调用挂起，等待 approve / reject。"""
    last_message = state["messages"][-1]
    # 检查大模型是否调用敏感工具
    sensitive_tool_call = None
    if hasattr(last_message, "tool_calls"):
        for tool_call in last_message.tool_calls:
            if tool_call["name"] in SENSITIVE_TOOLS:
                sensitive_tool_call = tool_call
                break
    if sensitive_tool_call:
        logger.warning("系统挂起，检测到敏感操作：%s 准备生成证明文件", sensitive_tool_call["name"])
        interrupt_message = "Agent 正在尝试生成包含薪资证明的文件，是否授权执行？（输入approve或者reject）"
        # 企业化第二阶段：开启鉴权时负载记录申请人 uid（供 /chat/resume 校验审批人 ≠ 申请人）；
        # AUTH_ENABLED=false 时保持旧的纯字符串负载，行为完全不变
        from auth.guard import build_interrupt_payload, is_auth_enabled

        if is_auth_enabled():
            from auth.context import get_current_identity

            applicant_uid = get_current_identity().uid or state.get("current_uid", "") or ""
            payload = build_interrupt_payload(interrupt_message, applicant_uid)
        else:
            payload = interrupt_message
        user_decision = interrupt(payload)
        if user_decision == "reject":
            reject_msg = ToolMessage(
                content="system:人工审批未通过，操作已被拒绝，请安抚用户并告知由于安全问题无法生成",
                name=sensitive_tool_call["name"],
                tool_call_id=sensitive_tool_call["id"],
            )
            return {"messages": [reject_msg]}
    logger.info("人工授权审批通过，允许放行")
    return {"messages": []}


class FactcheckResult(BaseModel):
    is_pass: bool = Field(description="如果ai的回答全忠于知识库原文输出True，捏造了数字或者政策则输出False")
    feedback: str = Field(description="如果False，则指出造假点，如果True，则输出Pass")


def _last_real_user_text(messages: List[BaseMessage]) -> str:
    """取最近一条真实用户输入（排除隐藏指令与审计打回消息）。"""
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            text = message.content or ""
            if text != IDLE_TIMEOUT_CMD and not text.startswith(AUDIT_FAIL_PREFIX):
                return text
    return ""


def _audit_reject_message(feedback: str) -> HumanMessage:
    """构造审计打回消息：注入隐藏反馈，驱动执行者带着原文重写。"""
    return HumanMessage(
        content=f"{AUDIT_FAIL_PREFIX} 事实错误反馈 {feedback}  请根据知识库原文重写，绝不包含虚假数据")


def fact_check_node(state: AgentState) -> dict:
    """审计者节点：后置事实检验（self-Reflection）。

    两层防线 + 一条兜底：
    1. 规则层（零成本、可解释）：抽取回答中的金额/天数/比例/职级等数值，与知识库原文比对，
       命中不一致即拦截，不必调用模型；
    2. 模型层：判断语义类偏差（条件被吃掉、原文未提及却被暗示）；
    3. fail-safe 兜底：审计输出解析失败时**不放行**，先打回重写，超限则转人工；
       反思轮次超上限（熔断）时不再输出未过审内容，改为对外兜底话术。
    """
    messages = state["messages"]
    last_message = messages[-1]

    # 逆序查找 RAG 召回的原文
    rag_context = ""
    for message in reversed(messages):
        if getattr(message, "name", "") == "search_hr_policy":
            rag_context = message.content
            break

    if not rag_context:
        # 无 RAG 原文可对照（如员工档案、年假余额等工具类回答），规则与模型都无从判定
        return {"messages": []}

    answer = last_message.content or ""
    question = _last_real_user_text(messages)
    loop_state = state.get("loop_state", 0)
    AUDIT_COUNTERS.record_checked()

    # ---- 第一层：规则层 ----
    violations = check_numbers(answer, rag_context, question)
    if violations:
        reason = "；".join(violations)
        AUDIT_COUNTERS.record_block("rule", reason)
        logger.warning("规则层拦截数字类幻觉：%s", reason)
        if loop_state > MAX_REFLECTION_LOOPS:
            AUDIT_COUNTERS.record_fallback_handoff()
            logger.warning("熔断：规则层拦截后重写次数超限，转人工兜底")
            return {"messages": [AIMessage(content=AUDIT_FALLBACK_MESSAGE)]}
        return {"messages": [_audit_reject_message(f"[规则层] {reason}")]}

    # ---- 第二层：模型层 ----
    logger.info("审计者介入，正在检查生成内容是否包含幻觉")
    parser = JsonOutputParser(pydantic_object=FactcheckResult)
    check_prompt = (
        f"你是一个冷酷的合规审计员。对比以下「知识库原文」和「AI生成的回复」。\n"
        f"「知识库原文」:\n{rag_context}\n"
        f"「AI生成的回复」:\n{answer}\n"
        f"严查金额、职级门槛、天数！发现捏造请判 False 并给出修改意见。\n"
        f"另外注意：回答是否吃掉了原文中的限制条件（如适用范围、职级门槛、时间限制）。\n\n"
        f"{parser.get_format_instructions()}"
    )

    try:
        response = get_checker_llm().invoke(check_prompt)
        result = parser.invoke(response)
        is_pass = bool(result.get("is_pass", True))
        feedback = str(result.get("feedback", "Pass"))
    except Exception as e:
        # fail-safe：解析失败不放行。先打回重写，超过容忍次数转人工兜底。
        AUDIT_COUNTERS.record_parse_error()
        logger.warning("审计输出解析失败（fail-safe 生效，不放行）：%s", e)
        if loop_state > MAX_REFLECTION_LOOPS + AUDIT_PARSE_RETRY_LIMIT:
            AUDIT_COUNTERS.record_fallback_handoff()
            return {"messages": [AIMessage(content=AUDIT_FALLBACK_MESSAGE)]}
        return {"messages": [_audit_reject_message(
            "[校验未完成] 审计结果无法解析，请严格依据知识库原文重写：只使用原文中出现过的数字、"
            "职级门槛与时间条件，不要外推。")]}

    if is_pass:
        AUDIT_COUNTERS.record_passed()
        logger.info("审计通过：规则层与模型层均未发现问题")
        return {"messages": []}

    AUDIT_COUNTERS.record_block("llm", feedback)
    logger.warning("发现幻觉，拦截并生成审计意见：%s", feedback)
    if loop_state > MAX_REFLECTION_LOOPS:
        AUDIT_COUNTERS.record_fallback_handoff()
        logger.warning("熔断：模型层拦截后重写次数超限，转人工兜底")
        return {"messages": [AIMessage(content=AUDIT_FALLBACK_MESSAGE)]}
    return {"messages": [_audit_reject_message(feedback)]}
