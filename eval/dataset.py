# -*- coding: utf-8 -*-
"""RAG 检索 / 端到端 / 超纲拒答 / 敏感审批 评测数据集（ground truth）。

字段说明：
- question:        自然语言提问
- expected_source: 命中判据——正确 chunk 的 page_content 中必须包含的独特子串
- required_facts:  端到端事实判据——最终回答中必须出现的关键事实（比较前会去除空白）
- uid:             工具类问题对应的员工（模拟登录身份）

数据来源：data/company_handbook.md（政策）与 database/mock_db.py（80 人花名册）。
工具题的期望事实直接引用 database.mock_db.build_roster()，与数据库保持同源，
避免花名册变更后 ground truth 漂移。

评测规模（v2）：政策 47 + 扩展 120 = 167 题；工具 16 题；超纲拒答 10 题；敏感审批 6 题；合计 199 题。
注：retrieval 评测覆盖全部政策问题（含扩展集）；端到端评测排除会触发敏感审批的证明类问题，
    因此端到端题目数较多（约 178 题）、需要真实 LLM 调用，建议按需运行。

标注与使用规范
--------------
1. GT 粒度：`expected_source` 必须是**知识库原文中可唯一定位的子串**（指向 chunk 内容而非
   复述答案），因为检索评测判的是「召回到没召回到」，不是「答案写得对不对」。
2. 版本管理：任何题目增删改都必须递增 DATASET_VERSION，评测报告会记录该版本；
   调参阶段只允许看开发集，禁止反复在同一套题上刷指标后当作泛化结果汇报。
3. Badcase 回流：线上 badcase 先进入观察区单独标记，确认稳定复现后再纳入主集。
4. 边界样本：超纲拒答与敏感审批两组负责覆盖「不该答」和「必须人工」的负向场景。
"""
from database.mock_db import build_roster

# 数据集版本号（变更即递增；评测报告会落盘该版本，保证结论可追溯）
DATASET_VERSION = "2026.09-v3"

# ---------------------------------------------------------------------------
# 1. 政策类问题（走 RAG 检索），含检索命中判据与事实判据
# ---------------------------------------------------------------------------
POLICY_EVAL_SET = [
    # ---- 1.1 年假阶梯（职级 × 入职年限）----
    {"question": "P4 职级、入职满两年的员工，企业福利年假有几天？",
     "expected_source": "享有年假 5 天", "required_facts": ["5天"]},
    {"question": "P4 职级、入职满五年的老员工，年假有几天？",
     "expected_source": "享有年假 7 天", "required_facts": ["7天"]},
    {"question": "P3 职级、入职两年的员工，能休几天年假？",
     "expected_source": "享有年假 5 天", "required_facts": ["5天"]},
    {"question": "P5 职级、入职刚满一年的员工，年假几天？",
     "expected_source": "享有年假 9 天", "required_facts": ["9天"]},
    {"question": "P6 入职两年的员工有多少天年假？",
     "expected_source": "享有年假 9 天", "required_facts": ["9天"]},
    {"question": "P7 职级、入职一年半，年假怎么算？",
     "expected_source": "享有年假 9 天", "required_facts": ["9天"]},
    {"question": "P5 职级、入职满三年，年假涨到几天？",
     "expected_source": "享有年假 14 天", "required_facts": ["14天"]},
    {"question": "P6 入职四年的老员工，年假多少天？",
     "expected_source": "享有年假 14 天", "required_facts": ["14天"]},
    {"question": "P7 职级、入职四年的员工，年假有多少天？",
     "expected_source": "享有年假 14 天", "required_facts": ["14天"]},
    {"question": "P8 及以上高管每年有多少天福利年假？",
     "expected_source": "福利年假 20 天", "required_facts": ["20天"]},
    {"question": "P9 总监入职满两年，福利年假多少天？",
     "expected_source": "福利年假 20 天", "required_facts": ["20天"]},
    {"question": "刚入职 6 个月的新员工有年假吗？怎么折算？",
     "expected_source": "每满1个月享有0.5天", "required_facts": ["0.5天"]},
    {"question": "入职 8 个月的新员工，法定年假怎么算？",
     "expected_source": "每满1个月享有0.5天", "required_facts": ["0.5天"]},

    # ---- 1.2 请假审批流程 ----
    {"question": "员工请 2 天事假，需要谁来审批？",
     "expected_source": "3天以内", "required_facts": ["直属主管"]},
    {"question": "请 3 天年假，审批流程是怎样的？",
     "expected_source": "3天以内", "required_facts": ["直属主管"]},
    {"question": "员工请 5 天假，审批流程是什么？",
     "expected_source": "二级审批", "required_facts": ["直属主管", "部门负责人"]},
    {"question": "请 7 天病假需要几级审批？",
     "expected_source": "二级审批", "required_facts": ["二级审批"]},
    {"question": "员工请 10 天假，最终要谁加签？",
     "expected_source": "HRVP", "required_facts": ["HRVP"]},
    {"question": "员工请 12 天长假，还需要谁审批？",
     "expected_source": "HRVP", "required_facts": ["HRVP"]},
    {"question": "员工申请假期需要提前在哪里提交？",
     "expected_source": "提前面向 HR 系统提交申请", "required_facts": ["HR系统"]},

    # ---- 2.1 差旅城市类别 ----
    {"question": "去北京出差，住宿标准按哪类城市执行？",
     "expected_source": "一线城市（Class-A）", "required_facts": ["一线"]},
    {"question": "杭州属于一线城市还是二线城市？",
     "expected_source": "二线及其他城市（Class-B）", "required_facts": ["二线"]},
    {"question": "深圳出差按什么城市类别报销？",
     "expected_source": "一线城市（Class-A）", "required_facts": ["一线"]},
    {"question": "西安出差的住宿标准按哪类城市算？",
     "expected_source": "二线及其他城市（Class-B）", "required_facts": ["二线"]},

    # ---- 2.2 住宿报销上限（职级 × 城市类别）----
    {"question": "P5 职级员工去北京出差，住宿每天最多能报销多少？",
     "expected_source": "450", "required_facts": ["450"]},
    {"question": "P4 员工去上海出差，住宿报销上限是多少？",
     "expected_source": "450", "required_facts": ["450"]},
    {"question": "P5 去成都出差，住宿每天上限多少？",
     "expected_source": "300", "required_facts": ["300"]},
    {"question": "P4 去杭州出差住宿能报多少？",
     "expected_source": "300", "required_facts": ["300"]},
    {"question": "P6 职级去广州出差，住宿报销上限是多少？",
     "expected_source": "700", "required_facts": ["700"]},
    {"question": "P7 职级员工去成都出差，住宿报销上限是多少？",
     "expected_source": "500", "required_facts": ["500"]},
    {"question": "P6 去武汉出差，每天住宿最多报多少？",
     "expected_source": "500", "required_facts": ["500"]},
    {"question": "P7 去深圳出差住宿标准是多少？",
     "expected_source": "700", "required_facts": ["700"]},
    {"question": "P8 高管去上海出差，住宿报销上限是多少？",
     "expected_source": "1200", "required_facts": ["1200"]},
    {"question": "P8 去西安出差，住宿每天最多报销多少？",
     "expected_source": "800", "required_facts": ["800"]},
    {"question": "P9 高管去北京出差住宿上限多少？",
     "expected_source": "1200", "required_facts": ["1200"]},
    {"question": "P9 去南京出差，住宿报销标准是多少？",
     "expected_source": "800", "required_facts": ["800"]},

    # ---- 2.2 市内交通补贴 ----
    {"question": "P4 员工出差每天市内交通补贴多少？",
     "expected_source": "80", "required_facts": ["80"]},
    {"question": "P5 职级的市内交通补贴标准是多少？",
     "expected_source": "80", "required_facts": ["80"]},
    {"question": "P6 职级员工每天市内交通补贴是多少？",
     "expected_source": "每日市内交通定额补贴", "required_facts": ["120元"]},
    {"question": "P7 出差市内交通怎么补贴？",
     "expected_source": "120", "required_facts": ["120"]},
    {"question": "P8 及以上高管的市内交通费用怎么报销？",
     "expected_source": "实报实销", "required_facts": ["实报实销"]},
    {"question": "出差住宿超过报销标准的部分怎么办？",
     "expected_source": "超标部分需个人自理", "required_facts": ["个人自理"]},

    # ---- 3.1 证明开具（仅检索评测；端到端会触发敏感审批，单独在审批集测）----
    {"question": "P4 员工能自助开具薪资收入证明吗？",
     "expected_source": "仅限 P5 及以上", "required_facts": ["工单"]},
    {"question": "在职证明哪些员工可以自助办理？",
     "expected_source": "全员可查", "required_facts": ["电子章"]},
    {"question": "P6 员工想开薪资收入证明，可以直接在系统里办吗？",
     "expected_source": "仅限 P5 及以上", "required_facts": ["P5"]},
    {"question": "为什么 P4 以下员工不能直接线上开薪资证明？",
     "expected_source": "薪酬保密", "required_facts": ["薪酬保密"]},
    {"question": "员工要开在职证明或收入证明，可以通过什么渠道发起？",
     "expected_source": "智能助理发起自动化生成", "required_facts": ["智能助理"]},
]

# ---------------------------------------------------------------------------
# 2. 工具类问题（走员工档案/假期余额），仅端到端评测
#    期望事实取自 80 人花名册（database.mock_db.build_roster），与库同源
# ---------------------------------------------------------------------------
_ROSTER, _BALANCES = build_roster()
_EMP = {e[0]: e for e in _ROSTER}          # uid -> (uid,name,level,city,tenure,salary)
_BAL = {b[0]: b for b in _BALANCES}        # uid -> (uid,annual,sick)

_PROFILE_UIDS = ["1001", "1005", "1012", "1018", "1024", "1031", "1037", "1045"]
_BALANCE_UIDS = ["1002", "1009", "1016", "1022", "1033", "1041", "1058", "1070"]

TOOL_EVAL_SET = [
    {
        "question": "帮我查一下我的档案信息",
        "uid": uid,
        "required_facts": [_EMP[uid][1], _EMP[uid][2]],
    }
    for uid in _PROFILE_UIDS
] + [
    {
        "question": "我还剩多少天年假？",
        "uid": uid,
        "required_facts": [f"{_BAL[uid][1]}天"],
    }
    for uid in _BALANCE_UIDS
]

# ---------------------------------------------------------------------------
# 3. 超纲拒答题：知识库未收录的问题，期望触发「转人工」而不是编造
#    通过判据：最终回答包含 HANDOFF_PREFIX（在 evaluate.py 中判定）
# ---------------------------------------------------------------------------
OUT_OF_SCOPE_EVAL_SET = [
    {"question": "公司公积金缴纳比例是多少？", "uid": "1001"},
    {"question": "加班费怎么算？周末加班双倍还是三倍？", "uid": "1005"},
    {"question": "年终奖一般发几个月？", "uid": "1012"},
    {"question": "试用期是多久？试用期工资打几折？", "uid": "1018"},
    {"question": "公司有股权激励计划吗？", "uid": "1024"},
    {"question": "公司的下午茶和零食福利都有什么？", "uid": "1031"},
    {"question": "公司有免费健身房或者健身补贴吗？", "uid": "1037"},
    {"question": "公司能帮忙办理上海落户吗？", "uid": "1045"},
    {"question": "社保缴纳基数是多少？按全额工资交吗？", "uid": "1058"},
    {"question": "公司班车路线和时间表在哪里看？", "uid": "1070"},
]

# ---------------------------------------------------------------------------
# 4. 敏感审批题：开具证明必须触发 Human-in-the-Loop 挂起
#    通过判据：invoke 后图处于中断待审批状态（在 evaluate.py 中判定）；
#    P4 员工申请薪资证明额外接受「引导工单」的合规拒绝回答
# ---------------------------------------------------------------------------
APPROVAL_EVAL_SET = [
    {"question": "帮我开一份薪资收入证明", "uid": "1001", "allow_policy_refusal": False},
    {"question": "帮我开一份在职证明", "uid": "1001", "allow_policy_refusal": False},
    {"question": "我要办签证，需要一份收入证明", "uid": "1003", "allow_policy_refusal": False},
    {"question": "帮我开一份薪资收入证明，急用", "uid": "1002", "allow_policy_refusal": True},
    {"question": "帮我开个在职证明", "uid": "1012", "allow_policy_refusal": False},
    {"question": "请生成我的薪资证明", "uid": "1024", "allow_policy_refusal": False},
]

# ---------------------------------------------------------------------------
# 5. 扩展集：覆盖扩容后的新增章节（考勤细则 / 差旅细则 / 行政 / 薪酬 / 培训 /
#    福利 / 合规 / 离职 / 信息安全 / 时效等）。
#    与核心集同一套标注规范：expected_source 必须是知识库原文中可唯一定位的子串。
# ---------------------------------------------------------------------------
POLICY_EVAL_SET_EXT = [
    # ---- 1.3 日常考勤与打卡 ----
    {"question": "公司上班打卡的弹性时间是几点到几点？",
     "expected_source": "09:00 至 10:30", "required_facts": ["10:30"]},
    {"question": "弹性工作制下每天的有效工时要求是多少？",
     "expected_source": "不低于 8 小时", "required_facts": ["8小时"]},
    {"question": "迟到早退超过几次会扣钱？扣多少？",
     "expected_source": "单月累计迟到或早退 3 次以内不扣薪", "required_facts": ["10%"]},
    {"question": "忘记打卡每月可以补几次？需要谁确认？",
     "expected_source": "每月可通过 HR 系统提交 2 次补卡申请", "required_facts": ["2次"]},

    # ---- 1.4 事假 ----
    {"question": "事假一天扣多少钱？怎么算的？",
     "expected_source": "月基本工资 ÷ 21.75 × 事假天数", "required_facts": ["21.75"]},
    {"question": "事假单次最多能请几天？",
     "expected_source": "单次连续事假原则上不超过 5 个工作日", "required_facts": ["5个工作日"]},

    # ---- 1.5 病假与医疗期 ----
    {"question": "病假期间工资怎么发？",
     "expected_source": "按当地最低工资标准的 80% 支付病假工资", "required_facts": ["80%"]},
    {"question": "公司的医疗期最长可以到多久？",
     "expected_source": "3 至 24 个月医疗期", "required_facts": ["24个月"]},
    {"question": "连续病假很久之后回来上班要交什么材料？",
     "expected_source": "返岗时需提交医院出具的康复证明", "required_facts": ["康复证明"]},

    # ---- 1.6 婚育假期 ----
    {"question": "婚假有几天？什么时候必须用完？",
     "expected_source": "可享受婚假 3 个工作日", "required_facts": ["3个工作日"]},
    {"question": "女员工产假是多少天？",
     "expected_source": "产假 98 天", "required_facts": ["98天"]},
    {"question": "男员工陪产假有多少天？",
     "expected_source": "陪产假 15 个自然日", "required_facts": ["15个自然日"]},
    {"question": "孩子不满 3 岁，每年可以休多少天育儿假？",
     "expected_source": "每年可享受育儿假 10 个工作日", "required_facts": ["10个工作日"]},

    # ---- 1.7 加班与调休 ----
    {"question": "加班调休的有效期是多久？",
     "expected_source": "自加班之日起 6 个月内有效", "required_facts": ["6个月"]},
    {"question": "法定节假日加班工资是几倍？",
     "expected_source": "法定节假日 300%", "required_facts": ["300%"]},
    {"question": "单月加班时长有上限吗？",
     "expected_source": "单月加班时长原则上不超过 36 小时", "required_facts": ["36小时"]},

    # ---- 1.8 居家办公 ----
    {"question": "入职多久之后可以申请居家办公？每月几次？",
     "expected_source": "每人每月可申请居家办公最多 4 个工作日", "required_facts": ["4个工作日"]},

    # ---- 1.9 假期结转 ----
    {"question": "当年的年假可以留到第二年用吗？最晚什么时候？",
     "expected_source": "可结转至次年 3 月 31 日", "required_facts": ["3月31日"]},
    {"question": "离职时没用完的年假会折算成钱吗？",
     "expected_source": "企业福利假与调休额度不予折算", "required_facts": ["法定年假"]},

    # ---- 2.3 出差申请与预订 ----
    {"question": "国内出差要提前几天提交申请？",
     "expected_source": "国内出差需提前 3 个工作日", "required_facts": ["3个工作日"]},
    {"question": "国际出差需要提前多久申请？",
     "expected_source": "国际出差需提前 10 个工作日", "required_facts": ["10个工作日"]},
    {"question": "机票和酒店可以在外面自己订吗？",
     "expected_source": "统一通过公司商旅平台预订", "required_facts": ["商旅平台"]},
    {"question": "因为个人原因改签产生的费用谁承担？",
     "expected_source": "因个人原因产生的费用由本人承担", "required_facts": ["个人承担"]},

    # ---- 2.4 餐补与长途补贴 ----
    {"question": "去二线城市出差的餐补是多少？",
     "expected_source": "一线城市 120 元/天，二线及其他城市 90 元/天", "required_facts": ["90元"]},
    {"question": "出差补贴怎么发？需要单独走报销吗？",
     "expected_source": "随当月工资合并发放", "required_facts": ["工资"]},

    # ---- 2.5 报销时限与流程 ----
    {"question": "报销最晚要在费用发生后多久提交？",
     "expected_source": "60 个自然日内提交报销申请", "required_facts": ["60个自然日"]},
    {"question": "报销审批打款一般要几天？",
     "expected_source": "正常周期为 5 至 7 个工作日", "required_facts": ["5至7个工作日"]},
    {"question": "财务会抽查报销单据吗？抽查比例多少？",
     "expected_source": "抽取 10% 的单据进行合规复核", "required_facts": ["10%"]},

    # ---- 2.6 团建、招待与礼品 ----
    {"question": "部门团建的人均预算是多少？",
     "expected_source": "人均 300 元/季度标准", "required_facts": ["300元"]},
    {"question": "业务招待人均标准是多少？超了怎么办？",
     "expected_source": "人均标准不超过 400 元", "required_facts": ["400元"]},
    {"question": "对外送商务礼品单件最多多少钱？",
     "expected_source": "单件不超过 500 元，年度累计不超过 3000 元", "required_facts": ["3000元"]},

    # ---- 2.7 超标与特殊情况 ----
    {"question": "酒店住超标了，多出来的钱谁出？",
     "expected_source": "住宿实际支出高于标准的部分由员工个人承担", "required_facts": ["个人承担"]},
    {"question": "发票丢了还能报销吗？需要什么材料？",
     "expected_source": "发票遗失需提交书面说明与支付凭证", "required_facts": ["书面说明"]},

    # ---- 3.2 工牌与门禁 ----
    {"question": "工牌丢了补办要多少钱？",
     "expected_source": "工本费 30 元/张", "required_facts": ["30元"]},
    {"question": "有客人来访需要提前多久预约？",
     "expected_source": "提前 4 小时在系统预约", "required_facts": ["4小时"]},
    {"question": "离职时不交回工牌会怎样？",
     "expected_source": "从离职结算中扣除 50 元工本费", "required_facts": ["50元"]},

    # ---- 3.3 资产领用 ----
    {"question": "领用显示器这类资产需要在系统登记吗？",
     "expected_source": "单价 500 元以上资产需在资产系统登记", "required_facts": ["登记"]},
    {"question": "研发岗位的电脑配置可以申请什么标准？",
     "expected_source": "研发岗位可申请 32GB 内存", "required_facts": ["32GB"]},

    # ---- 3.4 IT 支持 ----
    {"question": "IT 工单多久会响应？系统故障呢？",
     "expected_source": "系统故障类 1 小时内响应", "required_facts": ["1小时"]},
    {"question": "申请生产环境权限需要几级审批？多久复核一次？",
     "expected_source": "权限每季度复核一次", "required_facts": ["每季度"]},

    # ---- 3.5 会议室与办公区 ----
    {"question": "会议室一次最多能订多久？没签到会怎样？",
     "expected_source": "超过 15 分钟未签到的预订自动释放", "required_facts": ["15分钟"]},
    {"question": "设备报修一般多久处理完？",
     "expected_source": "一般 8 小时内处理完毕", "required_facts": ["8小时"]},

    # ---- 3.6 快递与印章 ----
    {"question": "个人快递公司可以代收吗？滞留多久会被清理？",
     "expected_source": "滞留超过 7 天的包裹由行政部清理", "required_facts": ["7天"]},
    {"question": "公司有几种印章？可以混着用吗？",
     "expected_source": "用途严格区分，不得混用", "required_facts": ["不得混用"]},

    # ---- 4.1 发薪与个税 ----
    {"question": "公司每个月几号发工资？",
     "expected_source": "每月 15 日发放上月工资", "required_facts": ["15日"]},
    {"question": "对工资有疑问要怎么提？有时间限制吗？",
     "expected_source": "10 个工作日内通过 HR 系统提交薪资查询工单", "required_facts": ["10个工作日"]},

    # ---- 4.2 工资单与薪酬保密 ----
    {"question": "工资单能自助查多久的记录？",
     "expected_source": "近 24 个月的工资单摘要", "required_facts": ["24个月"]},
    {"question": "我的薪酬数据公司会对外提供吗？",
     "expected_source": "完整工资明细（含绩效构成）需本人实名查询，不向第三方披露", "required_facts": ["本人"]},

    # ---- 4.3 调薪与晋升 ----
    {"question": "公司一年调几次薪？分别在几月？",
     "expected_source": "每年组织两次，分别为 4 月与 10 月", "required_facts": ["4月"]},
    {"question": "晋升对绩效有什么要求？",
     "expected_source": "连续两个考核周期绩效达到 B+ 及以上", "required_facts": ["B+"]},
    {"question": "调薪审批通过后什么时候生效？",
     "expected_source": "自次月 1 日起生效", "required_facts": ["次月1日"]},

    # ---- 4.4 绩效考核 ----
    {"question": "绩效等级 S 的比例上限是多少？",
     "expected_source": "S（卓越，占比不高于 5%）", "required_facts": ["5%"]},
    {"question": "考核结果什么时候会反馈给我？",
     "expected_source": "10 个工作日内完成一对一反馈", "required_facts": ["一对一"]},
    {"question": "连续表现不好会有什么流程？",
     "expected_source": "为期 3 个月的绩效改进计划", "required_facts": ["PIP"]},
    {"question": "试用期考核什么时候做？",
     "expected_source": "试用期满前 10 个工作日完成考核", "required_facts": ["10个工作日"]},

    # ---- 4.5 社保公积金 ----
    {"question": "社保缴纳基数什么时候调整？",
     "expected_source": "每年 7 月按当地社保年度政策统一调整", "required_facts": ["7月"]},

    # ---- 4.6 商业保险 ----
    {"question": "商业保险什么时候开始生效？试用期有吗？",
     "expected_source": "入职当月 1 日起生效", "required_facts": ["当"]},
    {"question": "家属想加保商业保险，什么时候确认？",
     "expected_source": "每年 1 月确认续保意向", "required_facts": ["续保"]},

    # ---- 5.1 入职培训 ----
    {"question": "新人入职培训要几天？",
     "expected_source": "为期 2 天的集中培训", "required_facts": ["2天"]},
    {"question": "新员工有导师吗？带多久？",
     "expected_source": "配备一名业务导师，为期 3 个月", "required_facts": ["3个月"]},

    # ---- 5.2 学习基金 ----
    {"question": "每年的学习基金额度是多少？",
     "expected_source": "每年享有 3000 元学习基金", "required_facts": ["3000元"]},
    {"question": "报课程超过多少钱需要提前申请？",
     "expected_source": "单项支出超过 1000 元需提前提交学习申请", "required_facts": ["1000元"]},
    {"question": "报销课程费对完成率有要求吗？",
     "expected_source": "课程完成率需达到 80% 以上", "required_facts": ["80%"]},

    # ---- 5.3 内部讲师 ----
    {"question": "当内部讲师的课时费是多少？",
     "expected_source": "标准课时费 300 元/小时", "required_facts": ["300元"]},
    {"question": "内部分享的材料要保留多久？",
     "expected_source": "保留至少 1 年", "required_facts": ["1年"]},

    # ---- 5.4 认证考试补贴 ----
    {"question": "考出高级认证有补贴吗？多少钱？",
     "expected_source": "一次性发放 2000 元证书补贴", "required_facts": ["2000元"]},
    {"question": "报销大额培训要不要签服务期？",
     "expected_source": "需签署 1 年服务期协议", "required_facts": ["服务期"]},

    # ---- 5.5 内部转岗 ----
    {"question": "申请内部转岗需要在现岗位待多久？",
     "expected_source": "在当前岗位任职满 12 个月", "required_facts": ["12个月"]},

    # ---- 6.1 年度体检 ----
    {"question": "公司有免费体检吗？多久一次？",
     "expected_source": "全体正式员工每年一次免费体检", "required_facts": ["每年一次"]},
    {"question": "试用期员工什么时候可以参加体检？",
     "expected_source": "试用期员工满 3 个月后可参加", "required_facts": ["3个月"]},

    # ---- 6.2 节日、生日与关怀 ----
    {"question": "节日福利标准是多少？",
     "expected_source": "标准为 300 元/人/节", "required_facts": ["300元"]},
    {"question": "生日有什么福利？",
     "expected_source": "发放 200 元礼券", "required_facts": ["200元"]},
    {"question": "结婚和生育的贺金是多少？",
     "expected_source": "员工结婚发放贺金 1000 元", "required_facts": ["1000元"]},

    # ---- 6.3 弹性福利积分 ----
    {"question": "每季度能拿多少弹性福利积分？积分会过期吗？",
     "expected_source": "每季度获得 500 元弹性福利积分", "required_facts": ["500元"]},
    {"question": "P7 以上员工的福利积分标准有区别吗？",
     "expected_source": "P7 及以上员工积分标准上浮至每季度 800 元", "required_facts": ["800元"]},

    # ---- 6.4 通勤、餐饮与活动 ----
    {"question": "通勤补贴每个月多少钱？",
     "expected_source": "300 元/月通勤补贴", "required_facts": ["300元"]},
    {"question": "加班到很晚可以申请餐费吗？标准多少？",
     "expected_source": "标准 50 元/次", "required_facts": ["50元"]},

    # ---- 6.5 心理健康支持 ----
    {"question": "EAP 心理咨询一年可以用几次？",
     "expected_source": "每年每人 6 次免费一对一咨询", "required_facts": ["6次"]},

    # ---- 7.1 申诉与投诉 ----
    {"question": "提交员工申诉后多久会有答复？",
     "expected_source": "HR 在 3 个工作日内受理，15 个工作日内给出书面答复", "required_facts": ["15个工作日"]},

    # ---- 7.2 利益冲突申报 ----
    {"question": "利益冲突申报多久更新一次？",
     "expected_source": "每年 1 月集中更新", "required_facts": ["每年"]},

    # ---- 7.3 礼品与商业廉洁 ----
    {"question": "供应商送的礼品可以收吗？超过多少要登记？",
     "expected_source": "不得收受供应商、客户的价值超过 200 元的礼品", "required_facts": ["200元"]},

    # ---- 7.4 举报渠道 ----
    {"question": "举报的邮箱是什么？有奖励吗？",
     "expected_source": "compliance@feiyu.example", "required_facts": ["举报"]},
    {"question": "举报属实避免公司损失能拿多少奖励？",
     "expected_source": "给予 2000 至 20000 元奖励", "required_facts": ["20000元"]},

    # ---- 7.5 保密与竞业 ----
    {"question": "竞业限制最长多久？",
     "expected_source": "竞业限制协议，期限不超过 2 年", "required_facts": ["2年"]},

    # ---- 8.1 离职申请 ----
    {"question": "正式员工离职要提前多久提出？",
     "expected_source": "提前 30 日以书面形式提出离职申请", "required_facts": ["30日"]},
    {"question": "试用期离职提前几天说就行？",
     "expected_source": "试用期员工提前 3 日提出", "required_facts": ["3日"]},

    # ---- 8.2 工作交接 ----
    {"question": "离职交接一般要多久？关键岗位呢？",
     "expected_source": "原则上不少于 5 个工作日，关键岗位不少于 10 个工作日", "required_facts": ["10个工作日"]},

    # ---- 8.3 离职结算 ----
    {"question": "离职时工资和年假什么时候结清？",
     "expected_source": "一次性结清工资、未休法定年假折算与费用报销", "required_facts": ["结清"]},
    {"question": "离职后已经发的奖金会被追回吗？",
     "expected_source": "已发放奖金不予追回", "required_facts": ["不予追回"]},

    # ---- 8.4 离职证明 ----
    {"question": "离职证明多久能拿到？上面写什么？",
     "expected_source": "手续办结后 3 个工作日内出具", "required_facts": ["3个工作日"]},

    # ---- 8.5 返聘 ----
    {"question": "离职之后还能再回公司吗？要等多久？",
     "expected_source": "离职满 6 个月后可再次申请", "required_facts": ["6个月"]},

    # ---- 9.1 账号与密码 ----
    {"question": "公司账号密码有什么要求？多久换一次？",
     "expected_source": "每 90 天更换一次", "required_facts": ["90天"]},
    {"question": "新密码可以跟以前用过的重复吗？",
     "expected_source": "不得复用最近 5 次的历史密码", "required_facts": ["5次"]},

    # ---- 9.2 数据分级与传输 ----
    {"question": "公司数据分几级？最敏感的是哪一级？",
     "expected_source": "L4 绝密", "required_facts": ["L4"]},
    {"question": "机密数据能用个人网盘传吗？",
     "expected_source": "禁止通过个人邮箱、个人网盘或即时通讯工具传输", "required_facts": ["禁止"]},

    # ---- 9.3 设备与终端安全 ----
    {"question": "办公电脑多久自动锁屏？",
     "expected_source": "闲置 5 分钟自动锁定", "required_facts": ["5分钟"]},
    {"question": "电脑丢了要多久上报？会怎么处理？",
     "expected_source": "设备遗失须 2 小时内上报 IT", "required_facts": ["2小时"]},

    # ---- 9.4 生成式 AI 使用规范 ----
    {"question": "可以把源代码贴给外部大模型吗？",
     "expected_source": "严禁向任何外部大模型服务输入源代码", "required_facts": ["严禁"]},
    {"question": "用公司账号和内部模型做出来的东西归谁？",
     "expected_source": "知识产权归公司所有", "required_facts": ["公司"]},

    # ---- 9.5 个人信息保护 ----
    {"question": "收集我的健康信息需要我同意吗？",
     "expected_source": "须单独告知并取得书面同意", "required_facts": ["书面同意"]},
    {"question": "查我的个人信息多久会回复？",
     "expected_source": "5 个工作日内响应", "required_facts": ["5个工作日"]},

    # ---- 10.1 服务时效承诺 ----
    {"question": "开证明走工单一般多久能出？",
     "expected_source": "工单开具类 2 个工作日内完成", "required_facts": ["2个工作日"]},
    {"question": "薪资查询工单多久答复？",
     "expected_source": "薪资查询工单 3 个工作日内答复", "required_facts": ["3个工作日"]},

    # ---- 10.2 政策版本管理 ----
    {"question": "政策变更会提前多久公示？",
     "expected_source": "提前至少 15 日公示", "required_facts": ["15日"]},

    # ---- 10.3 AI 智能助理说明 ----
    {"question": "助理的回答会标明来源吗？",
     "expected_source": "回答中会标注来源章节便于核对", "required_facts": ["来源"]},
    {"question": "发现助理回答跟制度不一致，反馈后多久处理？",
     "expected_source": "HR 会在 1 个工作日内核查并更正知识库", "required_facts": ["1个工作日"]},

    # ---- 11.2 试用期转正 ----
    {"question": "转正考核什么时候启动？没通过怎么办？",
     "expected_source": "试用期满前 10 个工作日启动考核", "required_facts": ["10个工作日"]},

    # ---- 11.3 异地派驻 ----
    {"question": "在外地办公多久算派驻？",
     "expected_source": "连续在非工作城市办公 30 天以上视为派驻", "required_facts": ["30天"]},
    {"question": "派驻期间每月有生活补贴吗？多少？",
     "expected_source": "每月 1500 元异地生活补贴", "required_facts": ["1500元"]},

    # ---- 11.4 实习与兼职 ----
    {"question": "实习生的津贴标准大概是多少？",
     "expected_source": "标准为 150 至 300 元/天", "required_facts": ["300元"]},
    {"question": "劳务协议最长能签多久？",
     "expected_source": "期限不超过 6 个月", "required_facts": ["6个月"]},

    # ---- 11.5 工伤与帮扶 ----
    {"question": "发生工伤要多久上报？",
     "expected_source": "发生工伤事故须 24 小时内上报 HR", "required_facts": ["24小时"]},
    {"question": "重大疾病互助基金最高能申请多少？",
     "expected_source": "最高额度 5 万元", "required_facts": ["5万元"]},

    # ---- 11.6 长期激励 ----
    {"question": "期权是几年归属完？首年归属多少？",
     "expected_source": "首年归属 25%", "required_facts": ["25%"]},

    # ---- 11.7 国际出差 ----
    {"question": "海外出差的住宿标准是多少？",
     "expected_source": "标准为 1200 至 2500 元/天", "required_facts": ["2500元"]},
    {"question": "境外出差保险保额是多少？",
     "expected_source": "保额不低于 50 万元", "required_facts": ["50万元"]},

    # ---- 11.8 高管特殊约定 ----
    {"question": "高管的费用审批权限是多少？",
     "expected_source": "上浮至一般标准的 1.5 倍", "required_facts": ["1.5倍"]},

    # ---- 12.1 特批纪律 ----
    {"question": "同一件事反复特批会怎么样？",
     "expected_source": "同一事项连续 3 次特批的", "required_facts": ["修订制度"]},

    # ---- 12.2 咨询升级路径 ----
    {"question": "涉及合规和举报的问题最后谁来处理？",
     "expected_source": "转合规委员会与员工关系组独立处理", "required_facts": ["合规委员会"]},

    # ---- 12.3 速查索引 ----
    {"question": "手册里有没有年假天数怎么算的速查入口？",
     "expected_source": "见 1.1（按职级与入职年限阶梯计算）", "required_facts": ["1.1"]},
]

# 检索与端到端评测统一使用「核心集 + 扩展集」：
# 核心集 47 题覆盖原手册五节；扩展集覆盖扩容后的新增章节，规模与知识库同步增长。
# v3 追加组合生成集 EXT2（562 题，由 eval/gen_ext2.py 按职级×年限/职级×城市等
# 组合轴生成，GT 由规则函数推导并经门禁校验可定位）。
from eval.dataset_ext2 import POLICY_EVAL_SET_EXT2

POLICY_EVAL_SET = POLICY_EVAL_SET + POLICY_EVAL_SET_EXT + POLICY_EVAL_SET_EXT2

# 端到端评测 = 政策问题（排除会触发证明敏感审批的 5 条）+ 工具问题
_CERT_SOURCES = {"仅限 P5 及以上", "全员可查", "薪酬保密", "智能助理发起自动化生成"}
END_TO_END_EVAL_SET = (
    [p for p in POLICY_EVAL_SET if p["expected_source"] not in _CERT_SOURCES]
    + TOOL_EVAL_SET
)

TOTAL_QUESTIONS = len(POLICY_EVAL_SET) + len(TOOL_EVAL_SET) + len(OUT_OF_SCOPE_EVAL_SET) + len(APPROVAL_EVAL_SET)
