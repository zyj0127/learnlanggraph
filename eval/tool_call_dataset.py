# -*- coding: utf-8 -*-
"""工具调用准确率评测集（Tool-Calling Eval Set）。

与 eval/dataset.py（检索/端到端 GT）、eval/tool_cases.py（轨迹级过程评测）互补：
本集评「单轮首轮工具决策」——给定用户问题（+登录 uid），LLM 第一次响应
应该调哪个工具、槽位该不该齐、该不该忍住不调。

用例字段：
- question:       自然语言提问
- uid:            模拟登录员工（系统提示词注入，与生产一致）
- expect:         "tool"    应调用指定工具（tool 字段）
                  "none"    不应调用任何工具（闲聊/政策问题，政策应走 search_hr_policy
                            的见 dataset.py 检索集，本集 none 专指「不该碰 HR 数据工具」）
                  "clarify" 信息不全，应先反问（期望不发起完整工具调用：
                            不调用工具 或 调用了但必填槽位缺失 都算通过——
                            槽位预检（tools/slot_filling.py）会兜底拦截后者）
- tool:           期望工具名（expect="tool" 时必填）
- slots:          期望槽位子集（对 tool_calls.args 做子集断言；值比较前 strip）

指标（eval/eval_tool_calls.py 计算）：
- tool_selection_accuracy：expect=tool 用例中首个工具调用命中期望工具的比例
- slot_completeness：       expect=tool 且带 slots 断言的用例中槽位全中的比例
- false_trigger_rate：      expect=none 用例中误调 HR 数据工具的比例
- clarify_accuracy：        expect=clarify 用例中未发起完整调用的比例

规模：profile 10 + balance 10 + cert 12 + leave 14 + clarify 8 + none 16 = 70 题。
标注规范同 dataset.py：题目增删改递增 TOOL_CALL_DATASET_VERSION。
"""

TOOL_CALL_DATASET_VERSION = "2026.10-v2"

# HR 数据工具（误触发判定的工具全集；search_hr_policy 属政策检索，不算误触发）
HR_DATA_TOOLS = {
    "get_employee_profile", "get_leave_balance",
    "generate_employment_certification", "apply_leave",
}

_U = "1001"

_PROFILE_CASES = [
    {"question": q, "uid": _U, "expect": "tool",
     "tool": "get_employee_profile", "slots": {"uid": _U}}
    for q in [
        "帮我查一下我的人事档案",
        "我的职级是多少？",
        "看看我的档案信息",
        "我现在基本工资多少？",
        "查询我的入职年限",
        "我在哪个城市办公？帮我确认下档案",
        "麻烦调出我的员工资料",
        "我的个人信息帮我看一下",
        "查下我的薪资和职级",
        "我想知道我档案里登记的入职时长",
    ]
]

_BALANCE_CASES = [
    {"question": q, "uid": _U, "expect": "tool",
     "tool": "get_leave_balance", "slots": {"uid": _U}}
    for q in [
        "我还有几天年假？",
        "帮我查下假期余额",
        "我的病假还剩多少天？",
        "今年年假用了多少还剩多少？",
        "看看我还有多少天假可以休",
        "查询我的年假剩余天数",
        "我剩多少病假？",
        "余额查询：年假",
        "帮我看看还能休几天",
        "我的假期额度还剩多少？",
    ]
]

_CERT_CASES = [
    *[
        {"question": q, "uid": _U, "expect": "tool",
         "tool": "generate_employment_certification", "confirm_ok": True,
         "slots": {"uid": _U, "cer_type": "employment"}}
        for q in [
            "帮我开一份在职证明",
            "我要办签证，需要在职证明",
            "帮我开在职证明，办社保落户用",
            "麻烦出一份在职证明，办银行卡用",
            "帮我生成在职证明文件",
            "需要一份证明我在职的材料",
        ]
    ],
    *[
        {"question": q, "uid": _U, "expect": "tool",
         "tool": "generate_employment_certification", "confirm_ok": True,
         "slots": {"uid": _U, "cer_type": "income"}}
        for q in [
            "帮我开一份收入证明",
            "贷款要用收入证明，帮我开一下",
            "我需要带薪资的在职收入证明",
            "开一份包含工资的证明",
            "帮我生成薪资收入证明",
            "办房贷需要收入证明，麻烦开一份",
        ]
    ],
]

_LEAVE_CASES = [
    *[
        {"question": q, "uid": _U, "expect": "tool", "tool": "apply_leave",
         "confirm_ok": True, "slots": s}
        for q, s in [
            ("帮我请 2026-11-02 到 2026-11-03 的年假，家中有事",
             {"uid": _U, "leave_type": "年假",
              "start_date": "2026-11-02", "end_date": "2026-11-03"}),
            ("我要请病假，2026-11-05 一天，感冒发烧",
             {"uid": _U, "leave_type": "病假",
              "start_date": "2026-11-05", "end_date": "2026-11-05"}),
            ("帮我提交事假申请：2026-12-01 至 2026-12-02，处理个人事务",
             {"uid": _U, "leave_type": "事假",
              "start_date": "2026-12-01", "end_date": "2026-12-02"}),
            ("我想休年假，从 2026-11-10 到 2026-11-11",
             {"leave_type": "年假",
              "start_date": "2026-11-10", "end_date": "2026-11-11"}),
            ("请两天年假，2026-11-16 到 2026-11-17，回老家",
             {"leave_type": "年假",
              "start_date": "2026-11-16", "end_date": "2026-11-17"}),
            ("2026-12-07 我请一天事假，去办过户",
             {"leave_type": "事假",
              "start_date": "2026-12-07", "end_date": "2026-12-07"}),
            ("病假申请：2026-12-08 到 2026-12-09，拔牙休息",
             {"leave_type": "病假",
              "start_date": "2026-12-08", "end_date": "2026-12-09"}),
            ("帮我请年假 2026-12-14 一天",
             {"leave_type": "年假",
              "start_date": "2026-12-14", "end_date": "2026-12-14"}),
        ]
    ],
    # 多轮合并语境（单句即含全部槽位的紧凑表达）
    {"question": "年假，11月20号，就一天，家里有事", "uid": _U,
     "expect": "clarify"},  # 缺年份：反问或给出可判定日期均可
    {"question": "请病假，下周三和周四，2026-11-18 到 2026-11-19", "uid": _U,
     "expect": "tool", "tool": "apply_leave",
     "confirm_ok": True,
     "slots": {"leave_type": "病假",
               "start_date": "2026-11-18", "end_date": "2026-11-19"}},
    {"question": "我要休 2027-01-04 到 2027-01-05 的年假", "uid": _U,
     "expect": "tool", "tool": "apply_leave",
     "confirm_ok": True,
     "slots": {"leave_type": "年假",
               "start_date": "2027-01-04", "end_date": "2027-01-05"}},
    {"question": "请事假，2026-11-25 至 2026-11-26，共两天", "uid": _U,
     "expect": "tool", "tool": "apply_leave",
     "confirm_ok": True,
     "slots": {"leave_type": "事假",
               "start_date": "2026-11-25", "end_date": "2026-11-26"}},
    {"question": "麻烦帮我请 2026-12-21 到 2026-12-22 的病假", "uid": _U,
     "expect": "tool", "tool": "apply_leave",
     "confirm_ok": True,
     "slots": {"leave_type": "病假"}},
    {"question": "提交请假申请：年假 2026-12-28 一天，事由休息", "uid": _U,
     "expect": "tool", "tool": "apply_leave",
     "confirm_ok": True,
     "slots": {"leave_type": "年假", "start_date": "2026-12-28"}},
]

# 信息不全：应先反问而非发起完整工具调用（槽位预检兜底）
_CLARIFY_CASES = [
    {"question": q, "uid": _U, "expect": "clarify"}
    for q in [
        "我想请假",
        "帮我请个假",
        "我要休年假",
        "请假，下周五",
        "我想请两天病假",
        "帮我开个证明",
        "开证明",
        "我要请假，过几天再说哪天",
    ]
]

# 反例：闲聊/政策/超纲问题不应触发任何 HR 数据工具
_NONE_CASES = [
    {"question": q, "uid": _U, "expect": "none"}
    for q in [
        "你好",
        "今天天气怎么样？",
        "你是谁？",
        "谢谢你的帮助",
        "差旅报销标准是什么？",
        "公司的年假政策是怎样的？",
        "加班有加班费吗？",
        "试用期多久？",
        "五险一金缴纳比例是多少？",
        "离职流程是什么？",
        "中秋节公司放几天假？",
        "报销需要哪些发票？",
        "给我讲个笑话",
        "1+1 等于几？",
        "公司食堂在哪里？",
        "附近有什么好吃的？",
    ]
]

TOOL_CALL_EVAL_SET = (
    _PROFILE_CASES + _BALANCE_CASES + _CERT_CASES
    + _LEAVE_CASES + _CLARIFY_CASES + _NONE_CASES
)


def validate_dataset(cases: list[dict] | None = None) -> list[str]:
    """数据集结构自检（dry-run 用），返回问题列表（空 = 通过）。"""
    cases = TOOL_CALL_EVAL_SET if cases is None else cases
    problems: list[str] = []
    valid_expect = {"tool", "none", "clarify"}
    for i, case in enumerate(cases):
        where = f"#{i}（{str(case.get('question'))[:20]}）"
        if not case.get("question"):
            problems.append(f"{where}：缺 question")
        if case.get("expect") not in valid_expect:
            problems.append(f"{where}：expect 非法 {case.get('expect')!r}")
        if case.get("expect") == "tool":
            if case.get("tool") not in HR_DATA_TOOLS:
                problems.append(f"{where}：tool 非法 {case.get('tool')!r}")
        if "slots" in case and not isinstance(case["slots"], dict):
            problems.append(f"{where}：slots 必须是 dict")
    n_none = sum(1 for c in cases if c.get("expect") == "none")
    if n_none < 5:
        problems.append(f"反例（expect=none）不足 5 条，当前 {n_none}")
    if not (50 <= len(cases) <= 80):
        problems.append(f"题量 {len(cases)} 不在 50-80 区间")
    return problems
