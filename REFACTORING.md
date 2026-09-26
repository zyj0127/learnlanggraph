# 企业级重构报告（REFACTORING）

> 对象：`learnlanggraph`（LangGraph + DeepSeek 企业 HR 智能助理）
> 原则：**重构不是重写**——图拓扑、检索策略、审批流、评测口径、API 契约（SSE 格式与字段名）全部保持不变。

## 一、重构前后结构对比

### 前（痛点）

```
config.py            # 路径常量散落多处重复计算
observability.py     # 单文件混合 callback / 定价常量 / 全局可变计数器
telemetry.py         # 单文件一身四职：埋点 + 统计 + Markdown 渲染 + CLI
streamlit_app.py     # 与 api/server.py 各自实现流式消费/首轮状态/审批恢复，埋点口径不一致
agent/rag_pipeline.py# 顶层 ~60 行可执行代码：加载 2 个 BGE 模型、建内存向量库、建 LLM
agent/nodes.py       # 顶层实例化 3 个 LLM 客户端
requirements.txt     # 全部 >= 无上限（已知会装出不兼容的 langchain 1.4.0）
test/                # 无 conftest.py，4 处 sys.path.insert 样板，testmilestone4.py 命名缺下划线
```

### 后（现状）

```
pyproject.toml       # 项目元数据 + 依赖 == 钉扎（唯一真源）+ 可选 extras（models / dev）
requirements.txt     # 由 pyproject.toml 同步钉扎，供 Docker 构建与 constraints.txt 配合
config.py            # 统一 Settings（pydantic-settings，get_settings() 惰性单例）+ 路径常量 + LLM 工厂
observability/       # 包：usage.py（UsageTracker）+ audit.py（AuditCounters，threading.Lock 线程安全）
telemetry/           # 包：sink.py（埋点写入/口径真源）+ metrics.py（统计）+ report.py（渲染）+ cli.py（python -m telemetry）
agent/session_runner.py # 公共会话执行层：build_turn_state + stream_turn（统一事件流与埋点口径）
agent/rag_pipeline.py   # 全懒加载：get_embeddings/get_reranker/get_expansion_llm/get_retriever（lru_cache）
agent/nodes.py          # LLM 懒加载工厂：get_executor_llm/get_llm_with_tools/get_checker_llm
test/conftest.py        # 统一处理导入路径；test_milestone4.py 命名规范化
```

## 二、逐项改动清单与理由

| # | 改动 | 涉及文件 | 理由 |
|---|------|----------|------|
| 1+4 | rag_pipeline 重资源改 lru_cache 懒加载工厂；重量级第三方 import（chromadb/BM25/sentence_transformers 等）下沉到函数体内；模块级 `embeddings/reranker/llm/retriever` 经 PEP 562 `__getattr__` 惰性解析兼容旧引用 | `agent/rag_pipeline.py` | 消除 import 副作用：`import agent.rag_pipeline` 不再加载模型/联网 |
| 1+4 | nodes 的 3 个顶层 LLM 实例改懒加载工厂，节点函数内取用 | `agent/nodes.py` | `import agent.nodes` 轻量化，单测/工具脚本不再被拖慢 |
| 2 | 新增 `pyproject.toml`（元数据 + == 钉扎 + extras）；`requirements.txt` 同步钉扎；chromadb/pillow/modelscope 挪入 `[models]` extra；constraints.txt 补 pydantic-settings/python-dotenv 约束；Dockerfile COPY 适配新包结构、去除示例中的个人绝对路径 | `pyproject.toml`、`requirements.txt`、`build_wheels/constraints.txt`、`Dockerfile` | 杜绝"装出不兼容 langchain 1.4.0"类漂移；核心链路减依赖 |
| 3 | config.py 升级为 pydantic-settings `Settings` + `get_settings()` 惰性单例；新增 TELEMETRY_DB 常量；observability 不再重复计算 PROJECT_ROOT | `config.py`、`observability/usage.py`、`telemetry/sink.py` | 路径与环境变量唯一真源 |
| 5 | 新增 `agent/session_runner.py`：`build_turn_state()`（首轮注入 uid + loop_state）与 `stream_turn()`（统一事件流：token/tool_call/tool_result/approval_required/done + 统一 log_turn 埋点）；server.py 只做 SSE 协议翻译；streamlit_app.py 只做渲染 | `agent/session_runner.py`、`api/server.py`、`streamlit_app.py` | 消除双前端编排重复；**streamlit 渠道从此也有埋点**（channel="streamlit"） |
| 6 | telemetry.py → telemetry/ 包四拆分，`telemetry/__init__.py` 与 `__main__.py` 保留旧 import 路径与 CLI 兼容；observability.py → observability/ 包（usage/audit），`__init__.py` 兼容 shim | `telemetry/`、`observability/`（删除两个旧单文件） | 单一职责 |
| 7 | AuditCounters 改 threading.Lock 线程安全，对外只暴露 record_*/reset/snapshot 方法（不再允许裸字段 `+=`）；nodes.py 同步改用新方法 | `observability/audit.py`、`agent/nodes.py` | FastAPI 多线程/Streamlit 脚本线程并发下计数不再混杂 |
| 8 | test/conftest.py 统一 sys.path；删除 7 个测试文件中的 sys.path 样板；testmilestone4.py → test_milestone4.py；pyproject 配 `testpaths=["test"]` | `test/` | 测试规范化 |
| 9 | 补齐类型注解（nodes/routers/mock_db 公共函数）；tools/hr_tools.py、database/mock_db.py 补模块 docstring；删除 routers.py 的 MAX_REFLECTION_LOOPS 残留 re-export（确认全仓无外部引用）；mcp_server docstring 去除硬编码个人路径（改用 sys.executable + cwd 示例）；rag_pipeline 重复 docstring 去重 | 多文件 | 可维护性 |
| 10 | README 第 5 节目录结构重写 + 新增 5.1「本地安装与运行」；Docker 示例路径泛化 | `README.md` | 文档与新结构一致 |

## 三、行为保持不变清单

- **图拓扑**：chatbot → human_review/tools/fact_checker 节点与条件路由逐行未动（`graph_builder.py`、`routers.py` 逻辑零改动）。
- **检索策略**：切块（chunking.py 未动）、BM25+向量混合、权重默认值 (0.6, 0.4)、HYBRID_WEIGHTS 解析、HyDE 扩写 prompt、重排取 Top-3、返回文本格式全部原样。
- **审批流**：interrupt 负载文案、approve/reject 语义、ToolMessage 打回内容不变。
- **评测口径**：fact_rules 规则、AuditCounters 字段与 snapshot() 输出键、telemetry 表结构（schema 逐字保留）与指标口径不变。
- **API 契约**：SSE 仍只有 `token`（字段 content）/ `approval_required`（thread_id、detail，detail 文案保持 server 原版）/ `done` 三类事件；/chat/stream、/chat/resume、/health 入参与错误码（400/409）不变。
- **兼容面**：`from telemetry import log_turn`、`from observability import UsageTracker, AUDIT_COUNTERS`、`from agent.rag_pipeline import retriever`（PEP 562）、`config.PROJECT_ROOT/DOC_PATH/EMPLOYEES_DB/CHECKPOINT_DB`、`get_chat_llm()` 等旧引用全部可用。

## 四、有意的行为对齐（需知晓）

1. **Streamlit 首轮状态构造**：原 streamlit 每一轮都发送 `loop_state: 0`，会重置反思熔断计数；现统一为 server 语义（仅首轮注入 uid 与 loop_state，后续轮只追加消息）。这是对齐修复，使熔断机制在 streamlit 渠道同样生效。
2. **Streamlit 埋点**：原 streamlit 无 log_turn；现经 session_runner 统一落埋点（channel="streamlit"）。telemetry.db 会新增该渠道数据——这是需求目标（埋点口径统一），非回归。

## 五、验证结果

- `python -m compileall .`：全量语法编译通过 ✅
- AST 静态循环 import 检测（全仓 *.py）：**无循环** ✅
- `python -m unittest test.test_fact_rules -v`：10/10 通过 ✅（纯规则、无重依赖）
- 受环境限制未运行：test_eval_gate（需 BGE 模型文件）、test_milestone2/3/4、test_api（需 langchain/fastapi 依赖）、eval 全套（需 API Key）。这些均为结构性兼容改动（import 路径经 shim/PEP 562 保留），建议在完整环境跑一次 `python -m pytest test/ -v` 与 `python eval/evaluate.py` 复核。

## 六、风险与后续建议

1. **eval/ 与 mcp_server 的 sys.path 引导保留**：`eval/evaluate.py`、`eval/benchmark.py`、`eval/eval_retrieval_sliced.py`、`eval/gen_ext2.py`、`eval/weight_sweep.py`、`mcp_client/ab_check.py`、`mcp_server/hr_tools_server.py` 保留了精简后的 sys.path 引导——它们以 `python <脚本路径>` / stdio 子进程方式直接运行时脚本目录不在项目根，引导是功能必需的。test/ 下的样板已全部删除（conftest.py 接管）。后续若要彻底消除，可把 eval 脚本统一改为 `python -m eval.xxx` 模块方式运行并同步文档。
2. **pydantic-settings 为新增依赖**（>=2.0,<3.0），已加入 pyproject/requirements/constraints；Docker 离线构建前需用 `scripts/fetch_wheels.py` 补齐其 wheel，否则离线分支会缺包。
3. **langchain-chroma 未在 constraints.txt 中钉扎**：pyproject 中未再将其列为核心依赖（内存向量库模式下 `from langchain_chroma import Chroma` 仍在 `_build_memory_vectorstore` 内延迟导入），如需持久化 Chroma 模式请安装 `.[models]` 并自行钉版本。
4. **AuditCounters 旧裸字段读写已移除**：如有外部脚本直接读写 `AUDIT_COUNTERS.checked` 等字段，需改用 `record_*()` / `snapshot()`；仓内引用已全部迁移。
5. **建议后续**：在 CI 加 `python -m unittest test.test_fact_rules -v`（零依赖冒烟）与 import 轻量性守卫（断言 `import agent.nodes` 不触发模型加载，可用 sys.modules 检查 sentence_transformers 未被导入）。

## 七、企业化第一阶段改造记录（2026-02-14）

> 目标：PostgreSQL 化 + pgvector 向量库 + Postgres checkpointer + LLM 网关 + Langfuse。
> 原则不变：**行为不变优先**——工具签名、SSE 契约、检索口径、审批流全部保持；
> 所有 pg 依赖路径均有 Settings 开关回退（SQLite / 内存），无 pg 环境测试不受影响。

| # | 改动 | 涉及文件 | 说明 |
|---|------|----------|------|
| 1 | docker-compose.yml：postgres(pgvector/pgvector:pg16) 主库 + 卷 + 健康检查；litellm / langfuse 可选 profile；app 留白注释 | `docker-compose.yml`、`docker/litellm/config.yaml`（新增） | litellm 模板含 deepseek 主 + 备用 failover 与限流注释 |
| 2 | Settings 新增 DATABASE_URL / USE_SQLITE_FALLBACK / VECTOR_STORE / EMBEDDING_DIM / LLM_* / LANGFUSE_* | `config.py` | 默认值即回退态（sqlite/memory/禁用），无 .env 行为不变 |
| 3 | LLM 工厂网关化：LLM_BASE_URL/API_KEY/MODEL 优先，缺项回退 DEEPSEEK_*；三实例温度差异不变 | `config.py` `get_chat_llm()` | OpenAI 兼容协议，可指 DeepSeek 官方或 LiteLLM/OneAPI |
| 4 | SQLAlchemy 2.0 ORM（employees/leave_balances 对齐 SQLite schema；certifications 扩展留痕表，工具暂不回写）+ 会话工厂 + 双后端 repository | `database/models.py`、`session.py`、`repository.py`（新增） | sqlalchemy 全部延迟导入，纯逻辑测试零触达 |
| 5 | Alembic 迁移（env.py 从 Settings 取连接串；0001_init 建表 + CREATE EXTENSION vector + ivfflat 余弦索引） | `alembic.ini`、`alembic/env.py`、`alembic/versions/0001_init.py`（新增） | 支持 `--sql` 离线 DDL 验证 |
| 6 | 80 人花名册 seed（复用 build_roster() 固定种子 42，幂等，--force 重灌） | `database/seed.py`（新增） | 与 SQLite init_db / eval/dataset.py 严格同源 |
| 7 | hr_tools 改走 repository.run_query（双后端 SQL 仅占位符不同），签名与文案逐字不变 | `tools/hr_tools.py` | mcp_server 复用路径无感 |
| 8 | checkpointer 三后端：postgres（psycopg PostgresSaver，失败回退 sqlite）/ sqlite（默认）/ memory | `agent/graph_builder.py` | 审批 interrupt 状态可入 pg 持久化 |
| 9 | 向量库 pgvector 化：kb_chunks 表（content 唯一约束幂等）+ 空表自动重建 + 余弦距离检索器（k=5，Document 结构一致）；BM25/混合权重/重排/Top-3 不变 | `database/kb_models.py`、`kb_store.py`（新增）、`agent/rag_pipeline.py` | VECTOR_STORE=memory 一键回退评测 |
| 10 | Langfuse callback handler 挂入 Graph 执行 callbacks；未启用/未装包/初始化失败全部静默降级；与 UsageTracker、telemetry 并存 | `observability/langfuse.py`（新增）、`agent/session_runner.py` | 主链路零感知 |
| 11 | 依赖钉扎：psycopg[binary]==3.2.3、sqlalchemy==2.0.36、alembic==1.14.0、pgvector==0.3.6、langgraph-checkpoint-postgres==3.1.0、langfuse==2.60.5 | `pyproject.toml`、`requirements.txt`、`build_wheels/constraints.txt` | 三处同步；Docker 离线构建前需 scripts/fetch_wheels.py 补 wheel |
| 12 | 文档：README 新增「8. 企业部署（第一阶段）」；.env.sample 补齐全部新变量（无真实值） | `README.md`、`.env.sample` | 含回退矩阵与环境变量清单 |

### 验证结果（第一阶段）

- `python -m compileall`：全量语法编译通过
- AST 循环 import 静态检查：无循环
- `python -m unittest test.test_fact_rules -v`：纯逻辑测试通过（不触达 pg 依赖）
- Alembic 离线 DDL（`alembic upgrade head --sql`）：PostgreSQL 语法生成验证通过
- 未运行：需真实 pg 实例 / API Key / 模型文件的链路（compose、seed、pgvector 检索、Langfuse trace）

### 遗留项（第二阶段）

- app 服务接入 docker-compose 编排（当前留白注释）
- certifications 表回写（证明开具留痕）+ 审批人与渠道字段
- LiteLLM 多实例限流的 Redis 引入；Langfuse v3 升级评估

## 八、企业化第二阶段改造记录（2026-02-15）：认证授权 + 工具身份上下文

> 目标：身份模型 + RBAC + FastAPI JWT 认证 + 审批人角色校验 + 审计留痕。
> 原则不变：**行为不变优先**——政策问答匿名可用；工具文案、SSE 契约、审批流拓扑不变；
> `AUTH_ENABLED=false` 时所有鉴权逻辑完全旁路（回到第一阶段行为）。

| # | 改动 | 涉及文件 | 说明 |
|---|------|----------|------|
| 1 | 新增 `auth/` 包：models（Identity + Role 枚举）、permissions（RBAC 纯函数，对齐 fact_rules 风格）、context（contextvars 请求身份）、guard（工具校验编排 + 固定拒答文案）、jwt_tokens（PyJWT 延迟导入） | `auth/`（新增） | models/permissions/context 零第三方依赖；规则可独立单测 |
| 2 | 工具层强制校验：三个 hr_tools 执行前经 contextvars 取身份走 permissions 判定，越权返回固定礼貌拒答文案（不抛异常），记审计计数 + telemetry 埋点 | `tools/hr_tools.py` | 签名与正常返回文案逐字不变；证明开具成功落 cert_issued 留痕 |
| 3 | FastAPI 认证依赖：Bearer JWT（HS256，JWT_SECRET；RS256/OIDC JWKS 扩展点注释在 jwt_tokens.py）；无 token=匿名；无效 token=401；员工身份以 token 为准防伪造 uid | `api/server.py` | SSE 契约与错误码（400/409）不变，新增 401/403 |
| 4 | 开发签发端点 `POST /auth/token`（uid+role 换 JWT，仅 AUTH_DEV_MODE=true 启用，默认关闭） | `api/server.py` | 生产关闭，由企业 SSO 签发 |
| 5 | 身份贯穿图执行：stream_turn 新增 identity 参数，执行期间 set contextvars、finally 复位；审批恢复路径同样恢复身份上下文 | `agent/session_runner.py` | 审批人与申请人身份分离 |
| 6 | 审批人角色校验：/chat/resume 仅 HR/ADMIN（403 拦截员工自审自批）；streamlit 审批按钮按角色禁用 | `api/server.py`、`streamlit_app.py` | 审批流拓扑不变 |
| 7 | Streamlit 登录侧栏：uid+角色登录/退出，未登录匿名可用政策问答；AUTH_ENABLED=false 时保持旧员工切换 UI | `streamlit_app.py` | UI 风格与既有侧栏一致 |
| 8 | 审计留痕：AuditCounters 新增 record_access_denied（snapshot 增 access_denied 键）；telemetry 新增 auth_events 表与 log_auth_event（越权/审批/开具各一笔，只记 uid/角色/动作/结果，不落 PII 值） | `observability/audit.py`、`telemetry/sink.py`、`telemetry/__init__.py` | 表结构为新增，session_events 口径不变 |
| 9 | Settings 新增 AUTH_ENABLED / AUTH_DEV_MODE / JWT_SECRET / JWT_ALGORITHM / JWT_EXPIRE_MINUTES；依赖钉扎 PyJWT==2.10.1（三处同步） | `config.py`、`pyproject.toml`、`requirements.txt`、`build_wheels/constraints.txt` | pyproject packages 补 auth 包 |
| 10 | 文档：README 新增「9. 认证授权」（token 获取、Header 用法、权限矩阵、SSO 扩展）；.env.sample 补 AUTH_*/JWT_* 变量 | `README.md`、`.env.sample` | — |

### 验证结果（第二阶段）

- `python -m compileall`：全量语法编译通过
- AST 循环 import 静态检查：无循环
- `python -m unittest test.test_fact_rules test.test_permissions test.test_auth_rbac -v`：全部通过（零外部依赖；真实工具级与 JWT 用例在无 langchain_core/PyJWT 环境下自动 skip）
- 未运行：需 pg 实例 / 模型文件 / API Key / FastAPI 依赖的链路（SSE 端到端、Langfuse trace），建议在完整环境跑一次 `python -m pytest test/ -v` 复核

### 遗留项（第三阶段）

- ~~审批人 ≠ 申请人校验~~（已补齐：interrupt 负载记录申请人 uid，resume 时比对，自审自批 403 + denied 留痕；旧负载无申请人 uid 时安全默认 HR 拒绝、ADMIN 放行，见 auth/guard.py `check_approval_allowed`）
- 身份与 db 员工表打通（当前 streamlit 登录为本地角色选择，未校验 uid 真实存在；接 SSO 后由 IdP 保证）
- ~~auth_events 的周报聚合~~（已补齐：metrics.auth_security_summary + report「安全与审计」小节 + CLI 自动包含，空表兜底不报错，见 test/test_auth_metrics.py）

## 九、Vue 3 前端新增（2026-02-15 后）：替换 Streamlit 展示层

> 目标：以 Vue 3 + TS + Vite 5 + Pinia 的 Web 前端（`web/`，hr-assistant-web）
> 替换 Streamlit 作为面向员工的主交互界面，对接 FastAPI 后端，不改任何后端
> Python 代码与 SSE 契约。

| # | 改动 | 涉及文件 | 说明 |
|---|------|----------|------|
| 1 | HTTP + SSE 客户端：POST 型 SSE（EventSource 不支持 POST），fetch + ReadableStream 手动解析 `data: {json}\n\n` 帧；401/403/409 错误透传后端 detail，兼容 FastAPI 422 detail 数组 | `web/src/api/client.ts` | 端点 `/auth/token`、`/chat/stream`、`/chat/resume`、`/health` 与后端逐一对齐 |
| 2 | Mock 演示模式：内置伪 JWT 签发、按字符切片流式输出、敏感词触发审批挂起、复刻 guard.py 审批人校验（员工/自审自批 403） | `web/src/api/mock.ts` | VITE_MOCK=true 或后端不可达时手动切换；事件序列与后端契约一致 |
| 3 | Pinia auth store：登录/退出，token+identity 持久化 localStorage；chat store：消息流、pendingApproval、审批预校验（角色 + 审批人 ≠ 申请人）、错误与后端不可达检测 | `web/src/stores/*.ts` | thread_id 口径与 Streamlit 一致（`session_{uid}_{8hex}`） |
| 4 | UI：侧边栏（登录面板、会话管理）、流式消息渲染（极简 markdown）、审批卡片（approve/reject，越权禁用并提示） | `web/src/App.vue`、`web/src/components/*.vue` | 未登录匿名可政策问答，与 RBAC 矩阵一致 |
| 5 | 「⏱️ 生成闲置会话总结」：发送 `__SYS_IDLE_TIMEOUT__`（不上屏），对齐 Streamlit 侧栏按钮 | `web/src/stores/chat.ts`、`web/src/types.ts` | 常量与 agent/constants.py 同源口径 |
| 6 | Vite dev proxy：`/api` → `VITE_PROXY_TARGET`（默认 http://localhost:8000），前缀重写 | `web/vite.config.ts`、`web/.env.example` | 生产部署由静态托管 + 反向代理接管 |
| 7 | 修复（收尾审计）：审批 403 时不再误推「已处理完毕」成功兜底文案，拒答文案直接落入聊天记录；parseError 兼容 422 detail 数组 | `web/src/stores/chat.ts`、`web/src/api/client.ts` | 审计发现的唯二行为缺陷 |

### 验证结果（Vue 前端）

- `npm run build`（vue-tsc --noEmit + vite build）：类型检查零错误，构建通过（node v24.15.0 / npm 11.12.1）
- dev server 冒烟：首页 200，`/src/main.ts`、`/src/style.css` 资源 200（验证后已停止 dev server，无后台残留）
- 未运行：真实后端 SSE 端到端联调（需 pg 实例 / 模型文件 / API Key）；Mock 模式可完整预览登录、流式问答与审批流

### 遗留项（Vue 前端）

- token 过期（expires_in）前端无自动刷新/踢出，过期后依赖 401 错误提示重新登录
- 生产部署形态（nginx 反代 `/api`、静态托管 dist）未提供现成配置
- 工具调用轨迹展示（Streamlit 的 show_trace）不适用于 Vue 版——HTTP SSE 契约不透出 tool_call/tool_result 事件，属有意取舍

## 十、生产部署编排（2026-02-15 后）：web 容器化 + compose 全栈

> 目标：把 Vue 前端接入第一阶段已有的 docker-compose 体系，补全留白的 app
> 服务，形成「postgres → migrate/seed → app → web」一键编排；不改后端代码，
> litellm / langfuse profile 行为不变。

| # | 改动 | 涉及文件 | 说明 |
|---|------|----------|------|
| 1 | web 多阶段 Dockerfile：node:20-alpine `npm ci` + `npm run build`（含 vue-tsc）→ nginx:alpine 托管 dist；构建参数 `VITE_API_BASE`/`VITE_MOCK`/`NPM_REGISTRY`；`.dockerignore` 排除 node_modules/dist | `web/Dockerfile`、`web/.dockerignore`（新增） | Mock 默认关闭；弱网可切 npm 镜像源 |
| 2 | nginx 配置：SPA fallback（try_files → index.html）、`/api/` 反代 `app:8000`（resolver + 变量 proxy_pass 避免启动时 upstream 未就绪失败）、SSE 三件套（proxy_buffering off / Connection 置空 / 读超时 1h）、assets 指纹长缓存 + index.html no-cache、gzip、安全响应头（CSP/X-Frame-Options/nosniff/Referrer-Policy） | `web/nginx.conf`（新增） | 反代前缀重写口径与 dev proxy 一致（/api/x → /x） |
| 3 | compose 补全 app 服务：build 根 Dockerfile、env_file .env + environment 显式覆盖 DATABASE_URL/USE_SQLITE_FALLBACK/VECTOR_STORE/CHECKPOINTER/模型路径（防 .env 的 localhost/Windows 路径泄漏进容器）、depends_on postgres 健康检查、模型挂载策略注释 | `docker-compose.yml` | 启动命令沿用镜像 CMD（uvicorn api.server:app） |
| 4 | compose 新增 web 服务：build ./web（构建参数生产口径）、depends_on app、8080:80 | `docker-compose.yml` | 外部只需暴露 8080 |
| 5 | 部署手册 DEPLOY.md：文字架构图、首次部署顺序、环境变量清单、运维命令、离线构建（fetch_wheels.py / npm 镜像 / docker save）、故障速查表 | `DEPLOY.md`（新增） | — |
| 6 | README「8.4 全栈编排」小节：启动顺序、服务说明、profile 组合、离线构建指引 | `README.md` | 保持 CRLF 行尾 |

### 验证结果（部署编排）

- 本机无 docker，`docker compose config -q` 与 `nginx -t` 无法实跑；替代验证：
  - docker-compose.yml 经 PyYAML 解析通过，结构审查：服务依赖链
    web→app→postgres(healthy) 完整，litellm/langfuse 仍带 profile 不默认启动，
    pgdata 卷保留
  - nginx.conf 静态审查：location 块配对、proxy_pass 变量 + resolver 用法、
    SSE 指令位置正确（需在容器环境用 `nginx -t` 终验，已列入遗留项）
  - web/Dockerfile 静态审查：两阶段 COPY 路径与 web/ 实际文件一一对应
- `python -m compileall` 全量语法编译通过（本次未改动任何 .py，确认无回归）

### 遗留项（部署编排）

- nginx.conf 未在真实 nginx 容器里 `nginx -t` 终验；compose 未实际 `up` 联调（本机无 docker）
- CSP 按当前构建产物（纯外联脚本）收紧到 `script-src 'self'`；若后续引入内联脚本需放宽
- app 首次启动构建 pgvector 索引需 1–2 分钟，web 的反代 502 窗口期已在 DEPLOY.md 故障表中说明
- 生产建议去除 app:8000 / postgres:5432 的宿主端口映射（仅内部网络），已在 DEPLOY.md 注明

## 十一、CI 流水线（2026-02-15 后）：GitHub Actions 质量门禁

> 目标：为重构 + 企业化改造（pgvector / RBAC / Vue 前端 / 生产编排）提供持续
> 质量保障。核心难点是测试分层——项目大量测试依赖 langchain / BGE 权重 /
> LLM Key，CI 必须让纯逻辑套件始终可跑、重依赖用例显式门禁开启。

| # | 改动 | 涉及文件 | 说明 |
|---|------|----------|------|
| 1 | ci.yml 六 job：python-test（3.11/3.12 矩阵，compileall + 循环 import + pytest）、eval-gate（离线切片：GT 可定位 + 配置契约）、eval-nightly（schedule 18:17 UTC / 手动，下载 BGE 权重跑 741 题基线门禁）、security（pip-audit 通报制 + npm audit 高危阻塞）、web-build（vue-tsc + vite build，dist artifact）、docker-check（compose config 阻塞 + hadolint 通报） | `.github/workflows/ci.yml`（新增） | torch 走 CPU 专用索引、constraints 钉版，与 Docker 构建同源 |
| 2 | marker 门禁机制：needs_models / needs_pg / needs_llm 默认跳过，CI_*_TESTS=1 环境变量开启；缺重依赖时对应测试模块 collect_ignore 整体跳过而非报错 | `test/conftest.py`、`pyproject.toml` | 纯逻辑套件零依赖可跑特性不变 |
| 3 | TestRetrievalGate 标 needs_models；test_hybrid_weights 契约测试补 langchain 缺失兜底（skipUnless，与 test_auth_rbac 同款）；pytest 导入做 shim，unittest 直跑零依赖特性保留 | `test/test_eval_gate.py` | 本地零依赖环境：51 passed / 7 skipped / 0 failed |
| 4 | 新增 AST 循环 import 检查脚本（模块级粒度，顶层 import 建图 + 迭代 DFS；延迟导入不计——是既定破环手段） | `scripts/check_import_cycles.py`（新增） | 初版包粒度误报 telemetry↔agent（telemetry.sink 只引 agent.constants），改模块粒度后 53 模块无环 |
| 5 | README 加 CI 徽章占位 + 「11. CI 流水线」章节（job 表、分层约定、安装口径） | `README.md` | 徽章待仓库推送后替换 <owner>/<repo> |

### 验证结果（CI）

- workflow YAML 解析 + 结构断言通过（6 job、矩阵、触发条件正确；本机无 actionlint）
- 本地模拟 CI 关键步骤全绿：compileall、check_import_cycles（53 模块无环）、
  `pytest test/ -v`（零依赖口径 51 passed / 7 skipped；needs_models 正确跳过）
- web：`npm run build` 复跑通过（vue-tsc 零错误）
- 未实跑：GitHub runner 上的完整 workflow（项目尚未推送远端）

### 遗留项（CI）

- 徽章 URL 为占位符，推送 GitHub 后需替换 <owner>/<repo>
- eval-nightly 的模型下载依赖 modelscope 可用性；失败时该 job 红但不影响 push/PR 门禁
- pip-audit 为通报制（continue-on-error），钉版依赖的历史 CVE 需人工评审处置
- test_api.py 等重依赖模块在本机零依赖环境整模块跳过；全依赖环境下的 pytest 结果建议在 CI 首跑确认

## 十二、企业化第二阶段（续）：推理服务化 + OpenTelemetry/Prometheus 监控

> 承接第八节（认证授权）。本节的两个主题回答的问题是：重推理资源如何从应用
> 容器卸载（嵌入/重排服务化），以及生产环境如何度量系统行为（链路 + 指标）。
> 核心难点是「静默降级」纪律——所有新组件未配置时必须零感知，不能让监控
> 设施反过来成为可用性风险。

| # | 改动 | 涉及文件 | 说明 |
|---|------|----------|------|
| 1 | 推理后端开关 `EMBEDDING_BACKEND`（local 默认不变 / tei）+ `TEI_EMBEDDING_URL` / `TEI_RERANKER_URL` | `config.py`、`agent/rag_pipeline.py` | tei 模式：嵌入走 TEI OpenAI 兼容 `/v1/embeddings`（langchain_openai 复用），重排走 TEI `/rerank`；lru_cache 懒加载约定不变 |
| 2 | TEI reranker 客户端封装（零重依赖，仅 urllib）：按 query 分组批量请求，`predict` 调用面与 CrossEncoder 完全一致，按 index 映射回原顺序 | `agent/tei.py`（新增） | 调用点（search_hr_policy）零改动 |
| 3 | compose `tei` profile：TEI CPU 版双服务（bge-small-zh-v1.5 / bge-reranker-base），共用 tei-models 卷，app 环境变量注释示例 | `docker-compose.yml` | 镜像 tag `cpu-latest`，生产建议钉具体版本；离线改绑定挂载宿主机 HF 缓存 |
| 4 | OTel tracer 工厂：`OTEL_EXPORTER_OTLP_ENDPOINT` 未配置/包缺失 → 返回 None 静默降级（对齐 langfuse 模式）；`stream_turn` 整轮包 span（channel / uid_hash 脱敏 / thread_id / latency / usage token） | `observability/otel.py`（新增）、`agent/session_runner.py` | uid 只落 sha256 前 12 位，不落明文 |
| 5 | Prometheus 指标出口：`/metrics` 端点 + HTTP 中间件（请求计数 / 延迟直方图）+ 审计与 RBAC 计数镜像（audit.py 口径不变，record_* 同时写 prometheus）；prometheus_client 未装时全模块 no-op | `observability/prom.py`（新增）、`observability/audit.py`、`api/server.py` | `/metrics` 自身不计数；指标标签不含 PII |
| 6 | compose `monitoring` profile：prometheus（抓取配置 `docker/prometheus/prometheus.yml`）+ grafana（预置数据源 provisioning） | `docker-compose.yml`、`docker/prometheus/`、`docker/grafana/`（新增） | Grafana :3001，admin 密码走环境变量 |
| 7 | 依赖钉版三处同步：prometheus-client==0.26.0、opentelemetry-sdk==1.44.0、opentelemetry-exporter-otlp-proto-http==1.44.0 | `pyproject.toml`、`requirements.txt`、`build_wheels/constraints.txt` | == 钉版纪律不变 |
| 8 | 单测：TEI reranker 封装（mock HTTP）+ 后端开关（mock 模型类，local 路径回归）+ otel 静默降级 + stream_turn 降级回归 + /metrics 端点与 RBAC 计数镜像 | `test/test_tei_backend.py`、`test/test_observability_otel.py`（新增） | 零外部依赖可跑；缺 langchain_openai/fastapi 时按既有 skipUnless 模式跳过 |
| 9 | 文档：README 9.6（推理服务化与监控）、DEPLOY 3.1（可选拓扑图） | `README.md`、`DEPLOY.md` | |

### 验证结果（第二阶段续）

- venv（[dev,models] 全依赖）：`pytest test/ -q` 83 passed / 4 skipped 全绿
  （含新增 16 条：TEI 封装与开关 10 条、otel/metrics 6 条）
- `compileall` + `scripts/check_import_cycles.py` 通过（56 模块无环）
- TEI / Prometheus / Grafana 容器未实起（本机无 docker）：compose 结构走 CI
  docker-check（config 校验）；TEI 客户端逻辑由 mock 单测覆盖

### 遗留项（第二阶段续）

- TEI 真实联调（起容器跑一轮检索质量门禁比对 local/tei 口径）待有 docker 的环境执行
- Grafana dashboard JSON 未预置（仅数据源 provisioning），可按需追加到
  `docker/grafana/provisioning/dashboards/`
- compose 中 TEI / Prometheus / Grafana 镜像 tag 为浮动/大版本钉版，生产环境
  建议钉到具体 patch 版本

## 十三、企业化第三阶段：Kubernetes 部署清单

> 承接第十二节。本节回答的问题是：compose 单机拓扑如何平移到 K8s 集群。
> 核心取舍是不引入 helm——plain manifests + kustomize 足够覆盖当前规模，
> 清单即文档，降低心智负担；密钥管理只给对接方案（ESO/sealed-secrets），
> 不实际引入 CRD，避免集群侧前置依赖。

| # | 改动 | 涉及文件 | 说明 |
|---|------|----------|------|
| 1 | Namespace + postgres（StatefulSet + headless Service + PVC 模板） | `k8s/namespace.yaml`、`k8s/postgres.yaml`（新增） | vector 扩展由 alembic 迁移负责（migrate Job），postStart 备选方案注释说明 |
| 2 | app（Deployment 2 副本 + Service + models-pvc） | `k8s/app.yaml`（新增） | readiness/liveness 打既有 `GET /health`（零依赖轻量端点，无需新增 /healthz）；resources 按 local 推理后端常驻 BGE 权重给 2Gi/4Gi；HPA 注释示例；模型三策略（RWX PVC / tei 卸载 / initContainer 现场下载）注释说明 |
| 3 | web（Deployment + Service + Ingress 示例） | `k8s/web.yaml`（新增） | SSE 关缓冲 annotation；TLS/cert-manager 注释示例 |
| 4 | ConfigMap（非敏感配置）+ Secret 模板（占位符，禁提交真实值） | `k8s/configmap.yaml`、`k8s/secret.example.yaml`（新增） | 与 config.py Settings 字段一一对应；ESO/sealed-secrets 对接点注释说明 |
| 5 | 初始化 Job：alembic upgrade head + database.seed（均幂等） | `k8s/migrate-job.yaml`（新增） | 独立 Job 显式执行而非启动钩子，失败可重跑；ttlSecondsAfterFinished 自动清理 |
| 6 | kustomization 串起全部资源（secret 不入 base） | `k8s/kustomization.yaml`（新增） | ESO/sealed-secrets 的 ExternalSecret 建议放 overlays/prod 另管 |
| 7 | CI 集成 kubeconform（钉版容器 v0.7.0，-strict 阻塞级）校验 k8s/ 全部清单 | `.github/workflows/ci.yml`（docker-check job） | -ignore-missing-schemas 放行 kustomization.yaml |
| 8 | 文档：DEPLOY「7. Kubernetes 部署」（分步命令 + 与 compose 差异 + 密钥管理三方案指引）、README 9.7 入口 | `DEPLOY.md`、`README.md` | |

### 验证结果（K8s）

- 全部 8 个清单 + ci.yml 通过 python yaml 多文档解析（kind 齐全：
  Namespace/StatefulSet/Deployment/Service/Ingress/Job/ConfigMap/Secret/Kustomization）
- 本机无 kubectl/docker：kubeconform 实跑由 CI docker-check job 承担（容器方式）
- 回归不破：`pytest test/ -q` 默认口径全绿、compileall、check_import_cycles
  （本阶段未改 Python 业务代码，server.py 探针复用既有 /health，无新增测试）

### 遗留项（K8s）

- 清单未在真实集群 apply 过：storageClassName、镜像仓库地址、Ingress 域名
  均为占位，首次部署需按集群实际调整
- TEI / monitoring 的 K8s 等价编排未做（compose profile 已覆盖单机场景；
  集群化时可把 tei 双服务与 prometheus/grafana 另建 manifests 或复用社区 chart）
- kubeconform 容器镜像 tag（v0.7.0）若上游变更需跟进；hadolint 同为通报制，
  镜像 Dockerfile 的 K8s 适配（非 root 运行等）未深入

## 十四、请假申请：从问答机器人到办事机器人（写操作 + 人工审批）

> 本节回答的问题是：如何在「不改图结构、不破 SSE 契约」的前提下新增一个
> 写操作业务。核心难点是图拓扑决定敏感工具只在审批通过后执行
> （human_review interrupt 先于 ToolNode），因此「预检 / pending 落库 /
> 履约扣款」三个阶段必须拆到两个位置，由 tools/leave_service.py 收敛为唯一实现。

| # | 改动 | 涉及文件 | 说明 |
|---|------|----------|------|
| 1 | `leave_requests` 表（id/uid/类型/起止/天数/事由/状态/审批人/时间戳）：ORM + alembic 0002 迁移 + SQLite 自愈建表（init_db）+ 双后端预置 2 条历史示例 | `database/models.py`、`alembic/versions/0002_leave_requests.py`、`database/mock_db.py`、`database/seed.py`（新增/修改） | SQLite 与 pg 的 DDL 逐字对齐；seed 幂等判据不变（employees 行数） |
| 2 | repository 写路径：run_execute（UPDATE/DELETE）+ run_insert_id（INSERT 取自增主键，pg 走 RETURNING） | `database/repository.py` | 占位约定与 run_query 同源（pg `:0` / sqlite `?`） |
| 3 | RBAC 新动作 `apply_leave`：员工仅本人、HR/ADMIN 可代申请、匿名拒（矩阵与 issue_cert 同构）；拒答文案 + 请假留痕（audit_leave_request，detail 不落事由自由文本） | `auth/permissions.py`、`auth/guard.py` | 审批恢复后工具以审批人（HR）身份执行，HR 代申请口径恰好兼容 |
| 4 | 业务逻辑层 leave_service：validate_request（类型/日期/天数）、check_annual_balance（预检）、create_pending、fulfill_approved（复核+扣减+置 approved）、mark_rejected | `tools/leave_service.py`（新增） | 节点与工具共用同一实现；测试按 service 序列驱动全链路 |
| 5 | apply_leave 工具（敏感工具）：RBAC → 参数/余额复核 → 履约；注册进 SENSITIVE_TOOLS 与 ALL_TOOLS | `tools/hr_tools.py`、`agent/constants.py`、`agent/nodes.py` | 履约在工具内（审批通过后执行），与开证明「通过后实际开具」同位置 |
| 6 | human_review_node 扩展：apply_leave 挂起前预检（余额不足直接回提示，**不进入审批**）+ pending 落库 + 审批文案含类型/日期/天数/事由；reject 分支置 rejected | `agent/nodes.py` | 图结构零改动，复用 interrupt/human_review 拓扑；防自审自批由 payload applicant_uid 自动适用 |
| 7 | SSE 透出审批详情：approval_required 的 detail 优先取 interrupt 负载文案（兼容旧字符串负载，回退固定文案） | `api/server.py` | 契约字段不变，前端审批卡片直接渲染请假详情 |
| 8 | Vue mock 演示：敏感词「请假/休年假/请病假/请事假」触发挂起，resume 按 cert/leave 场景分支应答 | `web/src/api/mock.ts` | ApprovalCard 复用 detail 渲染，无需结构化改动 |
| 9 | 测试 14 条：参数校验 4 + 工具级 5（本人成功/代他人被拦/匿名被拦/余额不足/HR 代申请）+ 审批链路 4（approve 扣余额/reject 不动/预检拦截/自审自批）+ pg needs_pg 冒烟 | `test/test_leave_request.py`（新增） | SQLite 实测（setUp 重建确定性花名册）；缺 langchain 时工具级用例 skipUnless |
| 10 | 文档：README 功能列表加办事写操作 | `README.md` | |

### 设计决策（请假）

- **履约位置**：图拓扑决定敏感工具只在审批通过后执行，故履约（余额复核 →
  年假扣减 → pending→approved）放在 apply_leave 工具体内——与开证明
  「审批通过后实际开具」同一位置；**扣减时机 = 审批通过瞬间**（pending 期间不锁余额，
  审批期间余额被占用的并发场景由履约时的复核兜底，不足则置 rejected 并提示）。
- **预检前置**：参数/余额预检在 human_review_node（挂起前），不足直接回
  ToolMessage 提示，不进入审批——对齐「余额不足不审批」需求且不打断审批拓扑。
- **pending 落库时机**：挂起 interrupt 之前（同节点），reject 分支置 rejected；
  找不到 pending 记录时（旧拓扑/旁路模式）履约与驳回都兜底直插对应状态行。
- **eval 数据集未动**：请假场景属工具/写操作链路而非检索题，加入检索评测集会
  改变 741 题基线（Hit@3/MRR），按「不破坏基线」原则不纳入 eval/dataset.py。

### 验证结果（请假）

- 全量 `pytest test/ -q`：**96 passed / 5 skipped**（原 83 条全绿 + 新增 13 条通过、
  1 条 needs_pg 默认跳过），36 subtests passed
- compileall + check_import_cycles（57 模块无环）通过
- 前端：vue-tsc --noEmit 零错误 + vite build 通过（本地内置 node 实跑）

## 十五、HR 管理台：审批队列 + 安全看板（第二条审批通道）

> 承接第十四节。本节回答的问题是：散落在各会话 interrupt 里的审批如何集中管理。
> 核心约束是「状态同源」——聊天内审批与队列审批是同一批工单的两条通道，
> `leave_requests.status` 为单一事实源：聊天里批过的工单在队列里自然消失，
> 队列批过的工单聊天 resume 时履约更新因 `status='pending'` 条件不命中而幂等。

| # | 改动 | 涉及文件 | 说明 |
|---|------|----------|------|
| 1 | leave_service 履约/驳回支持按 id 定向更新（可选 request_id 参数，WHERE 带 `status='pending'` 防重复履约） | `tools/leave_service.py` | 不传时保持按（uid+类型+起止）定位的旧行为，聊天路径零改动 |
| 2 | 管理台 API 三端点（仅 HR/ADMIN，旁路模式放行；匿名/员工 403）：`GET /api/admin/leave-requests`（join employees 取名，status/limit/offset）、`POST .../{id}/approve|reject`（404/409/自审自批 403）、`GET /api/admin/security-summary`（包装 auth_security_summary + 中英文标签映射） | `api/server.py` | 审批复用 leave_service 同一实现；审批人=当前身份并落 audit_leave_request 留痕；自审自批复用 check_approval_allowed 语义（工单 uid 即申请人 uid） |
| 3 | 前端管理台：App.vue 视图切换（不引入 vue-router，HR/ADMIN 登录后侧栏出现入口，身份降级自动回聊天）；AdminPanel.vue（工单表格 + 行内批准/拒绝 + 空队列兜底 + 安全看板卡片/Top 越权动作）；admin store；types/client/mock 三处同步（mock 内置 3 条演示工单，无后端可完整预览审批交互） | `web/src/App.vue`、`components/AdminPanel.vue`、`stores/admin.ts`、`types.ts`、`api/client.ts`、`api/mock.ts`（新增/修改） | 样式 scoped 自带，风格对齐现有 UI |
| 4 | 测试 10 条：员工/匿名 403、列表 join 姓名与 status 过滤、approve 扣余额置 approved、reject 不动余额、重复审批 409、404、自审自批 403、security-summary 结构断言 | `test/test_admin_queue.py`（新增） | TestClient + 测试 JWT 模式对齐 test_api；SQLite 实测；conftest collect_ignore 补零依赖兜底 |
| 5 | 文档：README 功能列表加管理台 | `README.md` | |

### 验证结果（管理台）

- 全量 `pytest test/ -q`：**106 passed / 5 skipped**（96 条存量全绿 + 新增 10 条）
- compileall + check_import_cycles（57 模块无环）通过
- 前端：vue-tsc --noEmit 零错误 + vite build 通过

## 十六、我的工单：员工自查视图（闭环请假体验）

> 承接第十五节。管理台解决了 HR 侧集中审批，本节补上员工侧的最后一环：
> 员工提交请假后能在哪里看到审批进度。核心约束仍是「状态同源」——
> 员工视图只读 `leave_requests`，与聊天审批、管理台共用同一事实源；
> 安全口径是 uid 只能来自 JWT，不接受 query/body 传 uid，结构上杜绝越权查他人。

| # | 改动 | 涉及文件 | 说明 |
|---|------|----------|------|
| 1 | `GET /api/my/leave-requests?status=`：uid 从 JWT identity 取（匿名 403），status 校验（非法值 400），join employees 取姓名，按提交时间倒序；旁路模式（AUTH_ENABLED=false）返回空列表 + hint，不伪造数据 | `api/server.py` | 插在 admin 端点之前；只读端点不落审计留痕（与管理台写操作区分） |
| 2 | 前端：MyRequests.vue（工单表格 + 状态徽章 pending 黄/approved 绿/rejected 红 + pending 显示「等待 HR 审批中」+ 空列表兜底 + 手动刷新）；App.vue 视图切换扩为 chat/admin/my，所有已登录角色侧栏可见入口，退出登录自动回聊天；client.ts 加 fetchMyLeaveRequests | `web/src/App.vue`、`components/MyRequests.vue`、`api/client.ts`（新增/修改） | 复用 AdminLeaveRequest 类型（含 employee_name），无新增类型 |
| 3 | mock 联动闭环：聊天里发起请假挂起时向 mockQueue 落 pending 行（动态 id），resume 批准/拒绝时联动改该行状态——Mock 演示模式下「聊天发起 → 审批 → 我的工单查看」全程可走通 | `web/src/api/mock.ts` | mockFetchMyLeaveRequests(uid, status) 按 uid 过滤 mockQueue 池；模块级 lastLeaveRequestId 串联挂起与恢复 |
| 4 | 测试 5 条：员工只见本人工单（看不到他人）、匿名 403、HR 登录也只见自己的（而非全员）、无工单员工空列表 200、status 过滤生效 | `test/test_my_requests.py`（新增）、`test/conftest.py` | TestClient + 测试 JWT 模式对齐 test_admin_queue；conftest collect_ignore 补零依赖兜底 |
| 5 | 文档：README 功能列表加「我的工单」 | `README.md` | |

### 验证结果（我的工单）

- 全量 `pytest test/ -q`：**111 passed / 5 skipped**（106 条存量全绿 + 新增 5 条），
  36 subtests passed
- compileall + check_import_cycles（57 模块无环）通过
- 前端：vue-tsc --noEmit 零错误 + vite build 通过
