# learnlanggraph

<!-- CI 徽章：推送到 GitHub 后把 zyj0127/learnlanggraph 替换为实际仓库路径 -->
[![CI](https://github.com/zyj0127/learnlanggraph/actions/workflows/ci.yml/badge.svg)](https://github.com/zyj0127/learnlanggraph/actions/workflows/ci.yml)
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
- **办事写操作（请假申请）：** `apply_leave(uid, leave_type, start_date, end_date, reason)` 把系统从问答机器人升级为办事机器人——员工发起年假/病假/事假申请，经人工审批（复用开证明同一 interrupt 拓扑与审批卡片）后生效：年假余额自动校验（不足不进入审批）与扣减，申请单全状态（pending/approved/rejected）落 `leave_requests` 表留痕。

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
├── pyproject.toml             # 项目元数据 + 依赖钉扎（唯一真源，== 版本）+ 可选 extras
├── config.py                  # 统一配置：Settings（pydantic-settings）+ 路径常量 + LLM 工厂
├── logging_config.py          # 统一日志（控制台 + 滚动落盘）
├── observability/             # 本地可观测性包
│   ├── usage.py               #   UsageTracker：token 用量 / 延迟 / 成本（LangChain callback）
│   └── audit.py               #   AuditCounters：幻觉审计指标（threading.Lock 线程安全）
├── telemetry/                 # 会话埋点包（每轮问答落库 telemetry.db）
│   ├── sink.py                #   埋点写入：schema / 意图分类 / log_turn（指标口径唯一真源）
│   ├── metrics.py             #   统计聚合：weekly_report
│   ├── report.py              #   Markdown 周报渲染
│   └── cli.py                 #   命令行：python -m telemetry [--days N] [--json] [--init]
├── streamlit_app.py           # Streamlit 前端（仅展示层，Graph 驱动走 session_runner）
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
├── agent/                     # 核心逻辑层（import 全程轻量，重资源一律懒加载）
│   ├── __init__.py
│   ├── constants.py           # 前后端共享的协议常量（隐藏指令/敏感工具/转人工词表）
│   ├── state.py               # AgentState 状态定义
│   ├── nodes.py               # 节点实现：执行者 / 人工审批 / 事实审计（LLM 懒加载工厂）
│   ├── routers.py             # 条件路由
│   ├── chunking.py            # 知识库切分：标题层切分 + Markdown 表格结构化提取
│   ├── rag_pipeline.py        # RAG：查询扩写 + HyDE + 混合检索 + 重排（模型/向量库懒加载）
│   ├── session_runner.py      # 公共会话执行层：API 与 Streamlit 共用流式驱动 + 统一埋点
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
├── test/                      # 测试层：conftest.py 统一处理导入路径 + 各 milestone 验证 + 评测门禁
├── .env                       # 配置文件：存放 API Keys (绝对不能提交到 Git)
├── .gitignore                 # Git 忽略文件配置
└── requirements.txt           # 依赖清单（由 pyproject.toml 同步钉扎，供 Docker 构建使用）
```

### 5.1 本地安装与运行

```bash
# 核心依赖（版本已钉扎，与评测基线及 Docker 镜像同源）
pip install -e .

# 可选 extras：
pip install -e .[models]   # chromadb / pillow / modelscope（模型下载、Chroma 持久化、测试绘图）
pip install -e .[dev]      # pytest 等测试工具

# 或传统方式（与 pyproject.toml 同步钉扎）：
pip install -r requirements.txt

# 运行
python database/mock_db.py                          # 首次：初始化 80 人花名册
uvicorn api.server:app --host 0.0.0.0 --port 8000   # FastAPI 服务
streamlit run streamlit_app.py                      # Streamlit 前端
python -m telemetry --days 7                        # 会话埋点周报

# 测试（pytest 自动经 test/conftest.py 处理导入路径）
python -m pytest test/ -v
python -m unittest test.test_fact_rules -v          # 纯规则单测：不加载模型、不调 LLM
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
  -v "<项目目录>\.local_models\BAAI\bge-small-zh-v1___5:/models/bge-small-zh-v1.5:ro" \
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



### 8. 企业部署（第一阶段）

第一阶段把「单容器 + SQLite + 内存向量库」升级为「PostgreSQL 主库 + pgvector +
LangGraph Postgres checkpointer + LLM 网关 + Langfuse 可观测性」，
所有新组件均有 Settings 开关可回退（默认回退态 = 原行为）。

#### 8.1 启动基础设施

```bash
# 最小集：只起 PostgreSQL 16 + pgvector 主库
docker compose up -d postgres

# 可选：LLM 网关（LiteLLM，DeepSeek 主 + 备用 failover）
docker compose --profile litellm up -d

# 可选：Langfuse 可观测性（复用主库，Web 控制台 http://localhost:3000）
docker compose --profile langfuse up -d
```

#### 8.2 迁移与种子数据

```bash
# 建表（employees / leave_balances / certifications / kb_chunks + vector 扩展）
alembic upgrade head

# 灌入 80 人花名册（与 build_roster() / 评测集同源，幂等）
python -m database.seed            # 已灌过则跳过
python -m database.seed --force    # 清空重灌

# 离线验证 DDL（无 pg 实例也可检查 SQL 语法）
alembic upgrade head --sql
```

应用首次以 pgvector 模式启动时，若 `kb_chunks` 为空会自动从
`data/company_handbook.md` 切块重建索引（幂等，`content` 唯一约束去重）。

#### 8.3 环境变量清单（新增，详见 .env.sample）

| 变量 | 默认 | 说明 |
|------|------|------|
| `DATABASE_URL` | `postgresql+psycopg://hr:hr@localhost:5432/hr_agent` | 主库连接串 |
| `USE_SQLITE_FALLBACK` | `true` | true=实体库走 SQLite（原行为）；false=走 PostgreSQL |
| `VECTOR_STORE` | `memory` | `memory`=内存向量库（原行为）；`pgvector`=kb_chunks 表 |
| `LANGGRAPH_CHECKPOINTER` | `sqlite` | `sqlite`（原行为）/ `postgres` / `memory` |
| `EMBEDDING_DIM` | `512` | kb_chunks.embedding 向量维度 |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 空 | LLM 网关（OpenAI 兼容）；空则回退 `DEEPSEEK_*` 旧口径 |
| `LANGFUSE_ENABLED` | `false` | 开启 Langfuse trace；密钥缺失/包未装时静默降级 |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | 空 / 空 / `http://localhost:3000` | Langfuse 凭据与地址 |

回退矩阵（无 pg 环境仍可全量跑通）：`USE_SQLITE_FALLBACK=true` +
`VECTOR_STORE=memory` + `LANGGRAPH_CHECKPOINTER=sqlite` 即为重构前的完整行为，
`test_fact_rules` 等纯逻辑测试不依赖任何 pg 组件（重依赖全部延迟导入）。

#### 8.4 全栈编排：app（FastAPI）+ web（Vue/nginx）

compose 已补全 `app` 与 `web` 两个服务（完整运维手册见根目录 `DEPLOY.md`）：

```bash
# 首次部署顺序（详见 DEPLOY.md）
cp .env.sample .env                        # app 的 env_file 指向它，必填
docker compose up -d postgres              # 1. 主库（健康检查通过后再下一步）
alembic upgrade head && python -m database.seed   # 2. 迁移 + 种子（宿主机执行）
python download_model.py                   # 3. 模型权重（已备则跳过），并解开
                                           #    compose 中 app.volumes 挂载注释
docker compose up -d --build app web       # 4. 构建并启动后端 + 前端

curl http://localhost:8000/health          # 后端直连
curl http://localhost:8080/api/health      # 经 nginx 反代
# 浏览器打开 http://localhost:8080
```

- **app**：build 根 Dockerfile；`environment` 显式覆盖 `DATABASE_URL` /
  `EMBEDDING_MODEL` / `RERANK_MODEL` 等（compose environment 优先级高于
  env_file，避免 `.env` 里的 localhost / Windows 路径泄漏进容器）；
  依赖 postgres 健康检查；模型权重只读挂载 `/models`（两种策略见 compose 注释）。
- **web**：build `web/Dockerfile`（node:20-alpine 构建 dist → nginx:alpine），
  构建参数 `VITE_API_BASE=/api`、`VITE_MOCK=false`；nginx 托管 SPA 并把
  `/api/*` 反代到 `app:8000`（SSE：关缓冲、读超时 1h，见 `web/nginx.conf`）；
  对外端口 `8080:80`。
- litellm / langfuse profile 不受影响，可与 app/web 任意组合：
  `docker compose --profile litellm up -d app web`。
- 离线构建：后端走 `scripts/fetch_wheels.py` 预下载 wheel（WSL/Linux）；
  前端弱网把 compose `web.build.args.NPM_REGISTRY` 切到 npmmirror。

### 9. 认证授权（第二阶段）

第二阶段在「行为不变优先」前提下加入身份模型与 RBAC：FastAPI 入口 JWT 认证、
工具层强制越权拦截、审批人角色校验、全链路审计留痕。
`AUTH_ENABLED=false` 时所有鉴权逻辑完全旁路，回到第一阶段行为。

#### 9.1 角色权限矩阵

| 动作 \ 角色 | 匿名 | 员工（EMPLOYEE） | HR | 管理员（ADMIN） |
|------------|------|----------------|----|----------------|
| 政策问答（RAG） | ✓ | ✓ | ✓ | ✓ |
| 查员工档案 | × | 仅本人 | ✓ | ✓ |
| 查假期余额 | × | 仅本人 | ✓ | ✓ |
| 开具证明 | × | 仅本人 | ✓ | ✓ |
| 人工审批（approve/reject） | × | × | ✓ | ✓ |

规则纯函数见 `auth/permissions.py`（可独立单测）；越权时工具返回固定礼貌拒答文案
（不抛异常、不打断 Graph），并记审计计数器与 `auth_events` 埋点各一笔；
`python -m telemetry` 周报的「安全与审计」小节已聚合授权事件（按类型/角色/时间窗计数，含越权 Top 动作）。

#### 9.2 获取 token（开发模式）

```bash
# .env 中设置 JWT_SECRET=... 与 AUTH_DEV_MODE=true（生产必须关闭本端点）
curl -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/json" \
  -d '{"uid": "1001", "role": "employee"}'
```

#### 9.3 调用方式

```bash
curl -N -X POST http://localhost:8000/chat/stream \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"uid": "1001", "question": "我还有几天年假？", "thread_id": "t1"}'
```

- 无 `Authorization` 头：匿名身份（政策问答可用，个人数据工具被 RBAC 拦下）。
- token 无效/过期：401；`/chat/resume` 审批：非 HR/ADMIN 一律 403，审批人与申请人同 uid（自审自批）同样 403 并留痕；升级前挂起的旧会话负载无申请人 uid 时按安全默认处理（HR 拒绝、ADMIN 放行）。
- SSE 事件契约不变（`token` / `approval_required` / `done`）。

#### 9.4 接企业 SSO（扩展点）

当前为 HS256 对称签名（`JWT_SECRET`）。接企业 SSO/OIDC 时：将
`JWT_ALGORITHM` 切为 RS256，把 `auth/jwt_tokens.py` 中 `decode_token` 的密钥
替换为从 IdP JWKS endpoint 拉取的公钥（推荐 `jwt.PyJWKClient`），并补充
`iss` / `aud` 校验；payload → Identity 的字段映射与下游 RBAC 口径不变。
届时 `/auth/token` 开发端点应整体下线。

#### 9.5 环境变量清单（新增，详见 .env.sample）

| 变量 | 默认 | 说明 |
|------|------|------|
| `AUTH_ENABLED` | `true` | 鉴权总开关；false 时完全旁路（旧行为） |
| `AUTH_DEV_MODE` | `false` | true 时启用 `POST /auth/token` 本地签发（生产必须 false） |
| `JWT_SECRET` | 空 | HS256 校验密钥；未配置时不放行任何 token |
| `JWT_ALGORITHM` | `HS256` | 签名算法（预留 RS256/OIDC JWKS 扩展） |
| `JWT_EXPIRE_MINUTES` | `120` | 开发签发 token 有效期（分钟） |

### 9.6 推理服务化与监控（第二阶段续：TEI + OpenTelemetry + Prometheus）

**嵌入/重排推理服务化（TEI）**：默认 `EMBEDDING_BACKEND=local`（进程内加载 BGE，
行为不变）；切 `tei` 后嵌入走 TEI 的 OpenAI 兼容 `/v1/embeddings`（复用
langchain-openai 客户端），重排走 TEI `/rerank`（`agent/tei.py` 零重依赖封装，
与 CrossEncoder 保持同一 `predict` 调用面），应用容器不再背 torch + 3.4GB 权重。
compose 提供 `--profile tei`（tei-embedding / tei-reranker 两个 CPU 服务）：

```bash
docker compose --profile tei up -d
# app 侧环境变量（compose 里有注释示例）：
#   EMBEDDING_BACKEND=tei
#   TEI_EMBEDDING_URL=http://tei-embedding:80
#   TEI_RERANKER_URL=http://tei-reranker:80
```

**OpenTelemetry 链路追踪**：配置 `OTEL_EXPORTER_OTLP_ENDPOINT` 后，
`session_runner.stream_turn` 整轮包一个 span（属性：channel、uid_hash 脱敏、
thread_id、latency、usage token 数）；未配置或 opentelemetry 包缺失时静默降级
（`observability/otel.py`，对齐 Langfuse 模式），主链路零感知。

**Prometheus 监控**：`GET /metrics` 暴露请求计数、延迟直方图（`hr_agent_http_*`）
与审计/RBAC 拦截计数（`hr_agent_audit_*` / `hr_agent_rbac_denied_total`，与
`observability/audit.py` 同一口径镜像写入，audit 原机制不变）。compose 提供
`--profile monitoring`（Prometheus 抓取配置在 `docker/prometheus/`，Grafana
预置数据源在 `docker/grafana/provisioning/`）：

```bash
docker compose --profile monitoring up -d
# Prometheus http://localhost:9090 ；Grafana http://localhost:3001
```

| 变量 | 默认 | 说明 |
|------|------|------|
| `EMBEDDING_BACKEND` | `local` | `local`（进程内 BGE）/ `tei`（TEI 推理服务） |
| `TEI_EMBEDDING_URL` / `TEI_RERANKER_URL` | 空 | TEI 服务根地址（tei 模式必填） |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 空 | OTLP HTTP endpoint；空则追踪静默降级 |
| `OTEL_SERVICE_NAME` | `hr-agent` | trace 的 service.name |

### 9.7 Kubernetes 部署（第三阶段）

`k8s/` 提供 plain manifests + kustomize（不引入 helm）：postgres StatefulSet、
app Deployment（2 副本，探针打 `/health`）、web Deployment + Ingress 示例、
migrate Job（alembic + seed，幂等）、ConfigMap + Secret 模板。

```bash
kubectl apply -f k8s/secret.yaml   # 由 secret.example.yaml 复制填值，或接 ESO/sealed-secrets
kubectl apply -k k8s/
```

完整步骤、模型挂载策略（PVC / TEI 卸载）与密钥管理指引见 DEPLOY.md
「7. Kubernetes 部署」；CI 的 docker-check 已集成 kubeconform 静态校验 k8s/ 清单。

### 10. Vue 前端（`web/`，hr-assistant-web）

Vue 3 + TypeScript + Vite 5 + Pinia 的 Web 前端，替换 Streamlit 作为面向员工的
主交互界面，对接 `api/server.py` 的 FastAPI 后端（HTTP 协议层，不动 Python 侧逻辑）。

#### 10.1 启动

```bash
cd web
npm install        # 首次
npm run dev        # 开发模式，默认 http://localhost:5173
npm run build      # 生产构建（含 vue-tsc 类型检查，输出 web/dist）
npm run preview    # 预览构建产物
```

dev server 已配置 proxy：`/api/*` → `http://localhost:8000/*`（前缀重写），
目标可用 `VITE_PROXY_TARGET` 覆盖。后端需先启动
（`uvicorn api.server:app --host 0.0.0.0 --port 8000`，并开启
`AUTH_ENABLED=true` + `AUTH_DEV_MODE=true` + `JWT_SECRET=...` 以启用登录签发）。

#### 10.2 环境变量（见 `web/.env.example`）

| 变量 | 默认 | 说明 |
|------|------|------|
| `VITE_API_BASE` | `/api` | API 基础路径（经 dev proxy 时保持 `/api`） |
| `VITE_MOCK` | `false` | true 时进入 Mock 演示模式，完全不依赖后端 |
| `VITE_PROXY_TARGET` | `http://localhost:8000` | dev proxy 目标（仅开发期生效） |

#### 10.3 Mock 演示模式

`VITE_MOCK=true`，或后端不可达时点击横幅「切换到 Mock 演示模式」。Mock 模式
内置：伪 JWT 签发（uid+role）、按字符切片模拟流式输出、敏感操作关键词
（证明/在职证明/收入证明）触发审批挂起、复刻 `auth/guard.py` 的审批人校验
（员工审批 403、自审自批 403）。SSE 事件序列与后端契约一致
（token / approval_required / done），可无后端完整预览登录、流式问答与审批流。

#### 10.4 功能口径（与 Streamlit 对齐）

- 登录（uid+角色换 JWT，Bearer 头，localStorage 持久化）、退出登录；
  未登录匿名可政策问答。
- POST 型 SSE 流式对话（fetch + ReadableStream 手动解析 `data: {json}\n\n` 帧），
  token 级渲染。
- interrupt → `approval_required` 事件渲染审批卡片；approve/reject 调
  `/chat/resume`；前端预校验（仅 HR/ADMIN、审批人 ≠ 申请人）禁用按钮，
  后端 403 拒答文案直接落入聊天记录。
- 「⏱️ 生成闲置会话总结」：发送 `__SYS_IDLE_TIMEOUT__` 指令（不上屏），
  与 Streamlit 侧栏按钮口径一致。
- 开启新会话（新 thread_id：`session_{uid}_{8hex}`）、清空显示。

与 Streamlit 的关系：两者均为纯展示层，共用同一 FastAPI 后端 / session_runner
实现；Streamlit 版（`streamlit run streamlit_app.py`）保留用于本地调试与
工具调用轨迹查看，Vue 版面向正式演示与部署。SSE 契约字段与后端逐一对齐，
未改动任何后端 Python 代码。


### 11. CI 流水线（GitHub Actions，`.github/workflows/ci.yml`）

push / PR 触发五个 job，schedule（每日 18:17 UTC）与手动触发追加完整评测门禁：

| Job | 触发 | 内容 | 门禁级别 |
|-----|------|------|----------|
| `python-test` | push/PR | Python 3.11/3.12 矩阵；compileall 语法门禁 → `scripts/check_import_cycles.py` 循环 import 检查 → `pytest test/ -v` | 阻塞 |
| `eval-gate` | push/PR | 评测门禁离线切片：GT 可定位 + 熔断/权重配置契约（不加载模型） | 阻塞 |
| `eval-nightly` | 定时/手动 | 下载 BGE 权重跑 741 题检索门禁（Hit@3/Hit@5/MRR 与 `eval/baseline.json` 比对，回归即红；不调 LLM 零 token 成本） | 阻塞 |
| `security` | push/PR | `pip-audit`（钉版依赖通报制，报告落 artifact）+ `npm audit --omit=dev`（高危阻塞） | npm 阻塞 / pip 通报 |
| `web-build` | push/PR | node 20：`npm ci` + `npm run build`（含 vue-tsc 类型门禁），dist 落 artifact | 阻塞 |
| `docker-check` | push/PR | `docker compose config -q` 校验 + hadolint（通报制） | compose 阻塞 |

测试分层约定（`test/conftest.py` + `pyproject.toml` markers）：

- `needs_models`（BGE 权重，~3.4GB）/ `needs_pg`（PostgreSQL）/ `needs_llm`
  （真实 LLM Key，消耗 token）三个 marker **默认跳过**，分别由
  `CI_MODEL_TESTS=1` / `CI_PG_TESTS=1` / `CI_LLM_TESTS=1` 显式开启；
- 缺 langchain/fastapi 等重依赖时，对应测试模块整体跳过（collect_ignore），
  纯逻辑套件（fact_rules / permissions / auth_rbac / auth_metrics /
  eval_gate 的 GT 与契约）保持零依赖可跑；
- CI 安装口径与 Docker 同源：torch 走 PyTorch CPU 专用索引，
  `-c build_wheels/constraints.txt` 钉版，「流水线里跑的就是评测过的那套」。
