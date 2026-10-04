# -*- coding: utf-8 -*-
"""多轮追问改写（conversational query rewriting）。

问题
----
多轮对话里用户追问依赖上下文：「差旅住宿标准多少？」→「那病假呢？」。
若把追问原文直接送检索，向量/BM25 都缺少主题词，召回失败。

方案
----
在 human_review_node（所有 tool_calls 的必经关卡）拦截 search_hr_policy 的
调用：先用启发式 needs_rewrite 判定「这是对对话历史的追问」，命中时复用
扩写 LLM（rag_pipeline.get_expansion_llm，懒加载）结合对话历史把问题改写为
自足完整问题，**只改写工具调用的 query 参数**：

- 用户消息原文不动（对话历史与前端展示不受影响）；
- 改写结果仅用于检索/重排，sources 溯源基于改写后的检索结果正常透出；
- 首轮或无历史、自足问题直接跳过（不多花 LLM 调用）；
- 改写 LLM 失败时回退原查询（降级不阻断主链路）。

纯函数（needs_rewrite / build_rewrite_prompt / format_history）零依赖可单测；
rewrite_followup 接受注入 llm，链路可用假 LLM 测。
"""
from typing import List, Optional, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from logging_config import get_logger

logger = get_logger(__name__)

# ---- 启发式触发策略（纯规则，零 LLM 成本）----
# 强承接标记：句首/句中出现即判定为追问（即使含主题词，如「那病假呢？」——
# 有主题无谓语，离开上下文无法检索）
_STRONG_MARKERS = ("那", "这个", "那个", "还有", "换成", "另外", "顺便",
                   "接着", "再说说", "它", "上面", "刚才")
# 弱标记：需同时满足「问题较短」才判定为追问
_WEAK_MARKERS = ("呢", "继续")
# 自足问题常见主题词：极短问题若含主题词则视为自足（如「病假天数？」）
_TOPIC_WORDS = (
    "年假", "病假", "事假", "差旅", "报销", "住宿", "餐补", "加班",
    "工资", "薪资", "社保", "公积金", "离职", "入职", "转正", "证明",
    "考勤", "绩效", "福利", "假期", "调休", "出差", "津贴", "补贴",
)
# 弱标记触发的问题长度上限
_SELF_CONTAINED_LEN = 20


def needs_rewrite(question: str, has_history: bool) -> bool:
    """判定当前问题是否是对话追问（需要结合历史改写后再检索）。

    规则（前提是存在对话历史，首轮一律不改写、零额外 LLM 调用）：
    1. 含强承接标记（那/这个/还有/换成……）→ 追问；
    2. 含弱标记（呢/继续）且问题较短（≤20 字）→ 追问；
    3. 问题极短（≤8 字）且不含任何主题词——短到不可能自足 → 追问。
    """
    if not has_history:
        return False
    q = (question or "").strip()
    if not q:
        return False
    if any(m in q for m in _STRONG_MARKERS):
        return True
    if len(q) <= _SELF_CONTAINED_LEN and any(m in q for m in _WEAK_MARKERS):
        return True
    return len(q) <= 8 and not any(t in q for t in _TOPIC_WORDS)


def format_history(messages: Sequence[BaseMessage], max_turns: int = 4) -> str:
    """把最近若干轮 Human/AI 对话格式化为改写上下文（纯函数）。

    只取真实用户与助手最终答复（跳过 ToolMessage/系统消息），
    每条截断 120 字，防止历史过长淹没改写指令。
    """
    turns: List[str] = []
    for m in messages:
        if isinstance(m, HumanMessage):
            role, text = "员工", m.content
        elif isinstance(m, AIMessage) and not getattr(m, "tool_calls", None):
            role, text = "助手", m.content
        else:
            continue
        text = (text or "").strip().replace("\n", " ")[:120]
        if text:
            turns.append(f"{role}：{text}")
    return "\n".join(turns[-max_turns * 2:])


def build_rewrite_prompt(history_text: str, question: str) -> str:
    """构造改写 prompt（纯函数）：输出自足完整问题，仅输出问题本身。"""
    return (
        "你是一名检索查询改写助手。下面是员工与 HR 助手的对话历史，"
        "员工的最新提问是一句依赖上下文的追问。\n"
        "请把最新提问改写成一个**自足、完整、不依赖上下文**的检索问题：\n"
        "- 补全追问中指代的主题（如「那病假呢？」→「病假的相关规定是什么？」）；\n"
        "- 保留员工的真实意图，不要扩宽或改变问题范围；\n"
        "- 只输出改写后的问题本身，不要输出任何解释、引号或标点包装。\n\n"
        f"【对话历史】\n{history_text or '（无）'}\n\n"
        f"【最新提问】{question}\n\n"
        "【改写后的完整问题】"
    )


def rewrite_followup(question: str, history: Sequence[BaseMessage],
                     llm=None) -> str:
    """按需改写追问为自足问题；不满足触发条件或改写失败时返回原问题。

    llm 可注入（测试用假 LLM）；None 时懒加载 rag_pipeline.get_expansion_llm
    （复用查询扩写专用 LLM，与 HyDE 同一实例，不新增客户端）。
    """
    question = (question or "").strip()
    if not needs_rewrite(question, has_history=bool(history)):
        return question
    try:
        if llm is None:
            from agent.rag_pipeline import get_expansion_llm

            llm = get_expansion_llm()
        prompt = build_rewrite_prompt(format_history(history), question)
        rewritten = (llm.invoke(prompt).content or "").strip().strip('"\'。')
        # 防御：改写为空、过长（LLM 跑题）或与原问题相同都不采纳
        if not rewritten or len(rewritten) > 100 or rewritten == question:
            logger.info("追问改写结果不可用，回退原查询：%r -> %r", question, rewritten)
            return question
        logger.info("追问改写：%r -> %r", question, rewritten)
        return rewritten
    except Exception as e:  # 改写失败降级不阻断主链路
        logger.warning("追问改写 LLM 调用失败，回退原查询：%s", e)
        return question


def rewrite_search_tool_calls(state_messages: List[BaseMessage],
                              llm=None) -> Optional[AIMessage]:
    """human_review 接入点：改写 search_hr_policy 的 query 参数。

    输入完整消息列表（末条应为带 tool_calls 的 AIMessage）；命中改写时返回
    一个**同 id** 的新 AIMessage（add_messages 按 id 替换，对话历史其他部分
    不受影响）；未命中或无需改写返回 None（调用方按原逻辑放行）。
    """
    last = state_messages[-1] if state_messages else None
    tool_calls = getattr(last, "tool_calls", None)
    if not tool_calls:
        return None

    # 改写上下文：当前轮用户提问之前的对话历史
    history = list(state_messages[:-2]) if len(state_messages) >= 2 else []
    changed = False
    new_calls = []
    for tc in tool_calls:
        if tc.get("name") == "search_hr_policy":
            args = dict(tc.get("args") or {})
            original = str(args.get("query") or "")
            rewritten = rewrite_followup(original, history, llm=llm)
            if rewritten != original:
                args["query"] = rewritten
                tc = {**tc, "args": args}
                changed = True
        new_calls.append(tc)

    if not changed:
        return None
    return AIMessage(
        content=last.content or "",
        tool_calls=new_calls,
        id=last.id,  # 同 id 替换：add_messages reducer 去重更新而非追加
    )
