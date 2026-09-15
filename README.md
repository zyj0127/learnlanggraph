# learnlanggraph
learnlanggraph
### 1. 业务价值与背景

在真实企业中，HR 和行政部门往往是“知识管理”的重灾区：

- **规则极度繁杂且碎片化：** 员工手册、差旅制度、报销规范、期权行权规则散落在几十个 PDF 和企业内网（Wiki/Confluence）中。
- **高度“千人千面”：** 同样是问“我的差旅住宿标准是多少？”，北京的 P7 员工和成都的 P5 员工，答案完全不同。传统的纯关键字搜索根本无法解决这个问题。
- **人力成本损耗严重：** HRBP 和行政人员每天被大量重复性、低价值的咨询（如“怎么开收入证明”、“年假还剩几天”）淹没，无法聚焦于人才盘点、组织发展等高价值战略工作。

**项目目标：** 打造一个 7x24 小时在线的智能 HR/行政助理，通过精准阅读规章制度并结合员工个人上下文，拦截并自动化处理 70% 以上的基础问询与简单行政服务，显著提升全员体验和 HR 效能。

### 2. 项目的边界在哪里？

在真实的软件工程中，界定“不做什么”比“做什么”更重要。

- **应该做：**
  - **政策咨询与解读：** 基于内部文档的 RAG 问答（如考勤、报销、福利制度）。
  - **基础自助服务：** 通过 API 调用（Tool Calling）实现查询年假余额、发起请假流程、自动生成带水印的《在职证明》PDF。
  - **智能路由：** 识别到员工情绪激动（如薪资纠纷、投诉），或遇到无法通过现有工具解决的问题时，优雅地转交人工。
- **坚决不做：**
  - **涉及复杂计算的薪酬系统：** 不要让 Agent 直接去算某个月的社保扣减或提成（极易出错且逻辑复杂）。
  - **核心人事数据的写操作：** 真实业务中 Agent 极少有权限直接修改员工薪资或职级，建议所有涉及数据的写操作，都处理为“生成审批表单”。
  - **招聘与绩效评估：** 避免引入伦理和偏见风险，专注于“服务”而非“评价”。

### 3. 核心功能模块设计

围绕我们已学的技术栈（LangChain/LangGraph/RAG），系统应包含以下模块：

- **用户上下文注入层：** 模拟系统登录，提取当前提问者的 Metadata（如姓名、部门、职级 P5、工作地上海）。
- **多源知识检索层 (RAG)：** 对《员工手册》等长文档进行分块、向量化存储，并引入重排（Reranker）提升匹配精度。
- **Agent 路由控制中枢 (LangGraph)：**
  - 如果是闲聊/问候，直接 LLM 回复。
  - 如果是查阅政策，路由给 `Policy_Search_Agent`。
  - 如果是查询年假，路由给 `Tool_Execution_Agent` 调用接口。
- **工具箱 (Tools)：** 预先定义好几个高频接口，如 `get_employee_profile(uid)`、`check_leave_balance(uid)`、`generate_income_certificate(name, salary)`。

### 4. 重难点与风险

#### 提升 RAG 的检索与回答准确率

这是该系统能否上线的生命线。HR 制度往往充满长难句和从句。

- **技术应对：** 不能简单地做按字数切分（Chunking），必须尝试语义切分或按文档层级（Markdown 标题结构）切分。可以展示对比：基础 Embedding vs 微调后的 Embedding，或者加入 BGE-Reranker 后召回率的提升。

#### 解决“条件约束”与“表格数据”的查询

HR 文档中充斥着复杂的表格（如不同职级/城市的差旅报销标准表）。传统的文本向量化对表格极其不友好，容易串行。

- **技术应对：** 对于表格数据，可以考虑将其转换为结构化的 JSON 或 Markdown 格式再存入向量库；或者在提问时，让大模型先将问题改写为 SQL 查关系型数据库（Text-to-SQL 结合 RAG）。

#### 动态上下文感知

大模型本身不知道在跟谁聊天。如果在提问时不带入个人信息，大模型就会给出模棱两可的答案（“取决于您的职级”）。

- **技术应对：** 每次 Prompt 构建时，必须在 System Message 中动态拼接：“当前用户属性：[北京, P7 研发]”，强制模型在检索到的文档中定位特定条款。

#### 严重的“幻觉”引发劳动纠纷

如果系统错误地告诉员工“你可以报销 5000 元”，但实际只能报销 500 元，会导致严重的业务灾难。

- **技术应对 (Traceability)：** 强制要求 Agent 在回答任何政策问题时，**必须且只能**基于检索到的 Context，并在前端 UI 提供**引用溯源（Citations）**，给出具体的文档链接和页码。兜底策略上，所有的政策回答末尾自动附加免责声明：“此内容由 AI 总结，最终解释权归 HR 部门所有。”

### 5. 项目工程化目录结构

```
hr_agent_project/
├── config.py                  # 全局配置：路径、环境变量、LLM 工厂
├── logging_config.py          # 统一日志（控制台 + 滚动落盘）
├── observability.py           # 本地可观测性：token 用量 / 延迟 / 成本
├── telemetry.py               # 会话埋点：每轮问答落库 telemetry.db
├── streamlit_app.py           # Streamlit 前端（仅展示层）
├── Dockerfile                 # 容器化：FastAPI 服务 + 健康检查（模型权重经挂载卷注入）
├── .dockerignore
├── scripts/                   # 宿主机构建辅助：fetch_wheels.py 预下载 Linux wheel
├── build_wheels/              # 预下载 wheel 缓存（*.whl 不入库）+ constraints.txt 版本钉扎
├── data/                      # 数据层：存放非结构化知识和静态资源
│   └── company_handbook.md    # 《员工手册》知识库（16509 字符，12 章 68 小节）
├── db/                        # 落盘产物：员工库 / checkpoint / 埋点库（向量库为内存版，不落盘）
├── database/                  # 数据库层：数据模型与连接管理
│   ├── __init__.py
│   └── mock_db.py             # SQLite 初始化与通用查询（80 人花名册，种子 42；
│                              #  规模变更后必须重跑 init_db() 与磁盘库保持同源）
├── tools/                     # 工具层：Agent 可以调用的所有外部能力
│   ├── __init__.py
│   └── hr_tools.py            # 档案查询 / 假期余额 / 证明开具
├── mcp_server/                # 工具层 MCP 化：stdio server（复用 hr_tools 原始逻辑）
│   └── hr_tools_server.py
├── mcp_client/                # MCP vs 直连双路径 A/B 一致性校验
│   └── ab_check.py
├── agent/                     # 核心逻辑层
│   ├── __init__.py
│   ├── constants.py           # 前后端共享的协议常量（隐藏指令/敏感工具/转人工词表）
│   ├── state.py               # AgentState 状态定义
│   ├── nodes.py               # 节点实现：执行者 / 人工审批 / 事实审计
│   ├── routers.py             # 条件路由
│   ├── chunking.py            # 知识库切分：标题层切分 + Markdown 表格结构化提取
│   ├── rag_pipeline.py        # RAG：查询扩写 + HyDE + 混合检索 + 重排
│   └── graph_builder.py       # LangGraph 装配入口（导出 hr_agent_app）
├── api/                       # 服务层
│   └── server.py              # FastAPI：/health + /chat/stream(SSE) + /chat/resume
├── eval/                      # 评测层
│   ├── dataset.py             # 评测集（ground truth，DATASET_VERSION 管理版本）
│   ├── tool_cases.py          # 工具调用轨迹（transcript）级用例与断言
│   ├── evaluate.py            # Hit@3 + 端到端 + 拒答 + 审批 + 轨迹
│   ├── weight_sweep.py        # 混合权重扫参
│   ├── baseline.json          # 检索质量门禁基线（test_eval_gate 消费）
│   └── benchmark.py           # token / 延迟 / 成本基准
├── test/                      # 测试层：各 milestone 验证脚本 + 评测门禁
├── .env                       # 配置文件：存放 API Keys (绝对不能提交到 Git)
├── .gitignore                 # Git 忽略文件配置
└── requirements.txt           # 依赖清单
```

### 6. 当前技术口径（2026.09-v4）

- **知识库切分**：Markdown 标题层级切分 + 表格结构化提取（2.2 报销标准表逐行转为
  带表头语义的独立切片，metadata 含 `table_row` / `table_kv`，引用可精确到「表格第 N 行」），
  共 72 个 chunk（69 小节切片 + 3 表格行切片）。
- **混合召回**：向量(BGE bge-small-zh-v1.5) + BM25，EnsembleRetriever 加权 RRF，
  线上默认权重 **向量 0.6 / BM25 0.4**（709 题扫参依据见 `eval/weight_sweep_result.json`）；
  CrossEncoder(bge-reranker-base) 精排取 Top-3。
- **评测集**：`2026.09-v4`，741 题（政策 709 + 工具 16 + 超纲拒答 10 + 敏感审批 6）。
- **实体库**：80 人花名册（`db/employees.db`，与 `build_roster()` 同源；不在版本库内）。

### 7. 容器化运行

```bash
# 可选：预下载 Linux wheel，让 docker build 转为完全离线（仅 WSL / Linux 可用）
python scripts/fetch_wheels.py

docker build -t hr-agent .

docker run -d --name hr-agent -p 8000:8000 \
  --env-file .env \
  -e EMBEDDING_MODEL=/models/bge-small-zh-v1.5 \
  -e RERANK_MODEL=/models/bge-reranker-base \
  -v "E:\code\py\learnlanggraph\.local_models\BAAI\bge-small-zh-v1___5:/models/bge-small-zh-v1.5:ro" \
  -v "C:\Users\<你>\.cache\modelscope\hub\models\BAAI\bge-reranker-base:/models/bge-reranker-base:ro" \
  hr-agent

curl http://localhost:8000/health   # {"status":"ok"}
```

两个 BGE 权重共约 3.4GB，**不打进镜像**，运行时经只读卷挂载到 `/models`。
员工库 `employees.db` 同理不入镜像，容器首次启动按固定种子自动生成
（与评测集 `build_roster()` 严格同源）。

上面两个 `-e` 不能省。`docker run` 的 `-e` / `--env-file` 优先级**高于**镜像内的 `ENV`，
而本项目的 `.env` 里也有 `EMBEDDING_MODEL` / `RERANK_MODEL` 且指向宿主机 Windows 路径
（本地直跑需要）。只给 `--env-file` 的话，镜像里设好的 `/models/...` 会被覆盖，容器启动即报
`OSError: Repo id must use alphanumeric chars ... E:\code\...`。实测对照过：仅 `--env-file`
取到 Windows 路径，`--env-file` 与 `-e` 同时给则 `-e` 胜出。

实测验收（2026-09-15，Windows + Docker 29.6.1）：

- `docker build` 成功，镜像 `hr-agent:latest` 3.05GB（含 CPU 版 torch，无任何 CUDA 组件）
- `docker run` 后 **20 秒** `/health` 返回 `{"status":"ok"}`，`/docs` 返回 Swagger UI，
  容器自报 `(healthy)`，常驻内存约 970MB
- 容器日志与 v4 口径逐条对齐：`72 个 chunk`、`混合检索权重（向量, BM25）= [0.6, 0.4]`、
  `生成内存向量库（不落盘）`、`db/employees.db 初始化成功`
- 端到端 SSE 冒烟：问「P5 员工去深圳出差，住宿费上限」，正确答出 `450 元/天`
  （命中 2.2 表格的 P4–P5 行），token 级流式推送正常

构建层面有三个已踩过的坑，都在 Dockerfile 里做了处理：

1. **torch 走 CPU 专用索引**。Linux 平台 PyPI 上的 torch wheel 会连带拉入
   `nvidia-cu12-*` / `triton` 等数 GB 的 CUDA 运行时依赖，纯 CPU 推理场景下既拖垮
   构建时间又让镜像虚胖一倍以上，弱网下还会直接把 `docker build` 挂死。PEP 440 中
   `2.13.0+cpu > 2.13.0`，因此两个索引同时给出时 pip 会优先选中 CPU 版。
2. **不写 `# syntax=docker/dockerfile:1`**。该指令会去 `docker.io` 拉 frontend 镜像，
   本机网络下拉不到会直接构建失败；而 BuildKit 内置的 frontend 其实已支持
   `RUN --mount`，够用。依赖层用 `type=cache` 复用 pip 下载缓存（大包中断后无需从零
   重下）、用 `type=bind` 把 `build_wheels/` 只读挂入（离线 wheel 不进镜像层）。
   实测 cache mount 的效果：torch 191.8MB 首次下载花了 13 分钟，第二次构建命中缓存只用 15 秒。
3. **依赖层必须带 `-c constraints.txt`**。否则容器内会装成 `langchain 1.4.0` /
   `mcp 2.2.0` / `sentence-transformers 6.0.1`（`mcp` 直接跨了大版本），
   而评测基线同源的是 `1.3.12` / `1.28.1` / `5.6.0` —— 「容器里跑的就是评测过的那套」
   这句话就不成立了。

> `scripts/fetch_wheels.py` 只能在 WSL / Linux 下跑：pip 的 `--platform` 只影响 wheel
> 选择、**不影响 environment marker 求值**，Windows 宿主上 `mcp` 的
> `pywin32>=310; sys_platform == "win32"` 仍会被判定为真而无解。Windows 上跳过这一步即可，
> Dockerfile 会自动走在线安装分支，且 cache mount 已保证重复构建很快。


