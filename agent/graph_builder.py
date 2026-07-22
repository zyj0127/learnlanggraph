import os
from typing import TypedDict, Annotated, List

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import JsonOutputParser
from langgraph.graph import StateGraph, START, END

from tools.hr_tools import get_employee_profile, get_leave_balance, generate_employment_certification
from agent.rag_pipeline2 import search_hr_policy


# 1. 定义全局共享状态(state)
class AgentState(TypedDict):  # 新
    messages: Annotated[List[BaseMessage], add_messages]
    current_uid: str
    loop_state: int


# 2. 初始化 LLM 语工具绑定
llm = ChatOpenAI(
    model=os.getenv('DEEPSEEK_MODEL_NAME_CHAT'),
    api_key=os.getenv('DEEPSEEK_API_KEY'),
    base_url=os.getenv('DEEPSEEK_BASE_URL'),
    temperature=0.0
)

tools = [get_employee_profile, get_leave_balance, generate_employment_certification, search_hr_policy]
llm_with_tools = llm.bind_tools(tools)

tools_node = ToolNode(tools)


# 定义执行节点
def chatbot_nodes(state: AgentState):
    """执行者节点意图理解，工具调用与生成内容生成"""
    messages = state.get('messages', [])

    # 首轮对话注入System Prompt
    if len(messages) == 1:
        system_msg = SystemMessage(
            content=f'你是飞羽科技的高级hr 智能助理\n'
                    f'当员工提问为uid为{state.get("current_uid")}\n'
                    f'请务必先调用 get_employee_profile获取该员工的工作属性，在回答具体问题\n'
                    f'必须基于工具的返回事实，绝不能编造数字或条件')

        messages = [system_msg] + messages
    response = llm_with_tools.invoke(messages)
    return {'messages': [response], 'loop_state': state.get('loop_state', 0) + 1}


class FactcheckResult(BaseModel):
    is_pass: bool = Field(description='如果ai的回答全忠于知识库原文输出Ture，捏造了数字或者政策则输出False')
    feedback: str = Field(description='如果False，则指出造假点，如果Ture，则输出Pass')


def fact_check_node(state: AgentState):
    """审计者节点，后置事实检验（self-Reflection）"""
    messages = state['messages']
    last_message = messages[-1]

    # 逆置查找RAG召回的原文
    rag_context = ''
    for message in reversed(messages):
        if getattr(message, 'name', '') == 'search_hr_policy':
            rag_context = message.content
            break

    if not rag_context:
        return {'messages': []}
    print('\n  审计者介入正在检查生成内容是否包含幻觉')

    checker_llm = ChatOpenAI(
        model=os.getenv('DEEPSEEK_MODEL_NAME_CHAT'),
        api_key=os.getenv('DEEPSEEK_API_KEY'),
        base_url=os.getenv('DEEPSEEK_BASE_URL'),
        temperature=0.7)
    parser = JsonOutputParser(pydantic_object=FactcheckResult)

    check_prompt = (
        f'你是一个冷酷的合规审计员。对比以下「知识库原文」和「AI生成的恢复」。\n'
        f'「知识库原文」:\n{rag_context}\n'
        f'「AI生成的恢复」:\n{last_message.content}\n'
        f'严查金额、职级门槛、天数！发现捏造请判 False 并给出修改意见。\n\n'
        f'{parser.get_format_instructions()}'
    )

    response = checker_llm.invoke(check_prompt)

    # 手动解析json
    try:
        result = parser.invoke(response)
        is_pass = result.get('is_pass', True)
        feedback = result.get('feedback', 'Pass')
    except Exception as e:
        print(f'审计异常json解析失败，默认放行原因：{e}')
        is_pass = True
        feedback = 'Pass'

    if is_pass:
        print('审计通过无幻觉')
        return {'messages': []}
    else:
        print(f'发现幻觉拦截生成审计意见：{feedback}')
        correction_msg = HumanMessage(
            content=f'SYSTEM AUTO FAILED 事实错误反馈 {feedback}  请根据知识库原文重写，绝不包含虚假数据', )
        return {'messages': [correction_msg]}


# 定义路由逻辑
def route_after_chatbot(state: AgentState):
    """Chatbot 输出后的路由判断"""
    last_message = state['messages'][-1]
    if last_message.tool_calls:
        return 'tools'
    else:
        return 'fact_checker'


def router_after_fact_check(state: AgentState):
    """审计完成后的路由判断"""
    last_message = state['messages'][-1]
    if isinstance(last_message, HumanMessage):
        if state.get('loop_state', 0) > 4:
            print('强制熔断，反思次数上限，放弃重写')
            return 'end'
        print('打回重写图路由指针到回流chatbot节点。。。')
        return 'chatbot'
    return 'end'


# 构建图
workflow = StateGraph(AgentState)

workflow.add_node('chatbot', chatbot_nodes)
workflow.add_node('tools', tools_node)
workflow.add_node('fact_checker', fact_check_node)

workflow.add_edge(START, 'chatbot')
workflow.add_conditional_edges('chatbot',
                               route_after_chatbot,
                               {'tools': 'tools',
                                'fact_checker': 'fact_checker', })
workflow.add_edge('tools', 'chatbot')
workflow.add_conditional_edges('fact_checker',
                               router_after_fact_check,
                               {'chatbot': 'chatbot',
                                'end': END})
hr_agent_app = workflow.compile()
