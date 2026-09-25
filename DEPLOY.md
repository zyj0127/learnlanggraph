# 生产部署手册（DEPLOY）

适用：企业化编排（docker-compose.yml）全栈部署 HR 智能助理 ——
PostgreSQL(pgvector) 主库 + FastAPI 后端（app）+ Vue 前端（web，nginx 托管）
+ 可选 LiteLLM 网关 / Langfuse 可观测性。

## 1. 架构（文字版）

```
                        浏览器
                          │  http://<host>:8080
                          ▼
                ┌───────────────────┐
                │  web (nginx:80)   │  Vue 静态产物 + SPA fallback
                │  /api/* ──────────┼──┐ 反代（SSE：proxy_buffering off，读超时 1h）
                └───────────────────┘  │
                                       ▼
                              ┌─────────────────┐
                              │ app (uvicorn    │  FastAPI：/auth/token
                              │  :8000)         │  /chat/stream /chat/resume（SSE）
                              └───────┬─────────┘
              ┌───────────────────────┼────────────────────────┐
              ▼                       ▼                        ▼
   ┌────────────────────┐  ┌────────────────────┐   ┌────────────────────┐
   │ postgres:16        │  │ litellm (可选      │   │ langfuse (可选     │
   │ +pgvector :5432    │  │  profile, :4000)   │   │  profile, :3000)   │
   │ 实体库/向量/checkpoint│  │ LLM 网关+failover  │   │ trace 可观测性     │
   └────────────────────┘  └────────────────────┘   └────────────────────┘
              ▲                                              │
              └────────────── langfuse 复用主库（独立 database）─┘
```

- 外部只暴露 web(8080)；app 的 8000、postgres 的 5432 可按需在 compose 中
  去掉 `ports` 映射改为纯内部网络（生产建议）。
- BGE 模型权重（约 3.4GB）不打进镜像，只读挂载进 app 容器的 `/models`。

## 2. 首次部署顺序

```bash
# 0. 准备环境变量（app 服务的 env_file 指向它，缺失会让 compose 报错）
cp .env.sample .env
#    至少填：DEEPSEEK_API_KEY（或走 litellm 网关）、JWT_SECRET、
#    AUTH_ENABLED=true、AUTH_DEV_MODE 按需要（生产应 false）

# 1. 主库
docker compose up -d postgres

# 2. 迁移 + 种子（在宿主机执行；DATABASE_URL 指向 localhost:5432）
alembic upgrade head
python -m database.seed

# 3. 模型权重（若宿主机 .local_models 已就绪则跳过）
python download_model.py
#    然后把 docker-compose.yml 中 app 服务的 volumes 注释解开、路径改对

# 4. 构建并启动后端 + 前端
docker compose up -d --build app web

# 5. 验收
curl http://localhost:8000/health          # {"status":"ok"}
curl http://localhost:8080/                # index.html
curl http://localhost:8080/api/health      # 经 nginx 反代 -> {"status":"ok"}
# 浏览器打开 http://localhost:8080，登录后提问验证 SSE 流式
```

可选组件：

```bash
docker compose --profile litellm up -d     # LLM 网关；.env 设 LLM_BASE_URL=http://litellm:4000
docker compose --profile langfuse up -d    # 观测台 http://localhost:3000；建 Project 拿 PK/SK 填回 .env
```

## 3. 环境变量清单（部署相关）

compose 层（根 `.env`，同时被 `${VAR}` 插值与 app 的 `env_file` 消费）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | hr / hr / hr_agent | 主库凭据；app 的 `DATABASE_URL` 由 compose 显式拼装覆盖 |
| `DEEPSEEK_API_KEY` | 空 | 直连 DeepSeek 时必填（或改用 litellm） |
| `JWT_SECRET` | 空 | JWT 验签密钥，生产必填 |
| `AUTH_ENABLED` / `AUTH_DEV_MODE` | true / false | 鉴权开关 / 开发签发端点（生产必须 false） |
| `LITELLM_MASTER_KEY` | sk-litellm-dev | 网关 master key |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | 空 | 观测性凭据 |

web 构建期参数（compose `web.build.args`，编译进静态产物）：

| 参数 | 默认 | 说明 |
|------|------|------|
| `VITE_API_BASE` | `/api` | 经 nginx 同源反代，勿改；仅直连外部后端时覆盖为完整 URL |
| `VITE_MOCK` | `false` | Mock 演示模式，生产必须 false |
| `NPM_REGISTRY` | npm 官方源 | 弱网切 `https://registry.npmmirror.com` |

完整应用级变量见 README「8.3」「9.5」与 `.env.sample`。

## 3.1 可选拓扑：推理服务化（TEI）与监控

默认拓扑下 app 容器进程内加载 BGE 权重（torch + 约 3.4GB 模型）。
企业化第二阶段提供两个可选 profile，把重资源从应用容器卸载：

```
                    ┌──────────────────────────────────────────┐
                    │ app (uvicorn :8000)                      │
                    │  EMBEDDING_BACKEND=tei 时：              │
                    └───────┬───────────────────┬──────────────┘
        /v1/embeddings      │                   │ /rerank
                            ▼                   ▼
              ┌────────────────────┐  ┌────────────────────┐
              │ tei-embedding :80  │  │ tei-reranker :80   │  ← --profile tei
              │ bge-small-zh-v1.5  │  │ bge-reranker-base  │     (TEI CPU 镜像)
              └────────────────────┘  └────────────────────┘
                            │
                            ▼ /metrics 抓取（15s）
              ┌────────────────────┐  ┌────────────────────┐
              │ prometheus :9090   │─→│ grafana :3001      │  ← --profile monitoring
              └────────────────────┘  └────────────────────┘
```

- **TEI（`--profile tei`）**：`ghcr.io/huggingface/text-embeddings-inference` CPU 版，
  embedding / reranker 各一个服务，共用命名卷 `tei-models`（首次启动 TEI 现场下载，
  重启复用；离线环境改绑定挂载宿主机已备好的 HF 缓存，见 compose 注释）。
  app 侧配置 `EMBEDDING_BACKEND=tei` + `TEI_EMBEDDING_URL=http://tei-embedding:80` +
  `TEI_RERANKER_URL=http://tei-reranker:80`（compose 的 app 服务里有注释示例）。
  切换后 app 容器不再加载 torch/transformers，启动与扩缩容显著变快。
  生产建议把镜像 tag 从 `cpu-latest` 钉到具体版本。
- **监控（`--profile monitoring`）**：Prometheus 抓取 app 的 `GET /metrics`
  （配置 `docker/prometheus/prometheus.yml`）；Grafana 预置 Prometheus 数据源
  （`docker/grafana/provisioning/`），admin 密码走 `GRAFANA_ADMIN_PASSWORD`。
  指标口径：HTTP 请求计数/延迟直方图（`hr_agent_http_*`）、审计与 RBAC 拦截计数
  （`hr_agent_audit_*` / `hr_agent_rbac_denied_total`，与 audit.py 同一口径）。
- **链路追踪（OTLP）**：配置 `OTEL_EXPORTER_OTLP_ENDPOINT` 指向任意 OTLP HTTP
  收集器（如 Jaeger/Tempo 的 4318 端口）即开启；未配置静默降级。

## 4. 常用运维命令

```bash
docker compose ps                          # 状态与健康度
docker compose logs -f app                 # 后端日志（SSE/审批留痕在其中）
docker compose logs -f web                 # nginx 访问/错误日志
docker compose up -d --build web           # 前端发版（只重建 web）
docker compose up -d --build app           # 后端发版
docker compose restart app                 # 改 .env 后重启生效
docker compose down                        # 停全部（pgdata 卷保留）
docker compose down -v                     # 危险：连数据卷一起删
# 备份主库：
docker compose exec postgres pg_dump -U hr hr_agent > backup.sql
```

## 5. 离线 / 弱网构建注意

- **后端镜像**：先在 WSL/Linux 跑 `python scripts/fetch_wheels.py` 预下载全部
  Linux wheel 到 `build_wheels/`，Dockerfile 检测到非空即走 `--no-index`
  完全离线安装；为空则回退阿里云镜像（torch 走 CPU 专用索引）。
  Windows 宿主跑不了 fetch_wheels.py（pip environment marker 限制），
  直接在线构建即可，pip cache mount 保证重复构建很快。
- **前端镜像**：`npm ci` 是唯一网络依赖；弱网在 compose 的
  `web.build.args` 里把 `NPM_REGISTRY` 切到 npmmirror。完全离线环境可在
  有网机器 `docker save hr-agent-web | gzip > hr-agent-web.tar.gz` 搬运镜像。
- **基础镜像**：`python:3.13-slim`、`node:20-alpine`、`nginx:alpine`、
  `pgvector/pgvector:pg16` 需能拉取；离线环境提前 `docker pull` + `docker save`。
- **模型权重**：约 3.4GB，务必提前用 `download_model.py` 备好并挂载，
  不要在容器内现场下载。

## 6. 故障速查

| 现象 | 排查 |
|------|------|
| app 启动报 `Repo id must use alphanumeric` | `.env` 的 Windows 模型路径泄漏进容器：确认 compose `environment` 里 `EMBEDDING_MODEL`/`RERANK_MODEL` 覆盖为 `/models/...` 且 volumes 已挂载 |
| web 打开但提问无响应/整段返回 | 确认经 8080 访问（nginx 已关 SSE 缓冲）；若绕开 nginx 直连 8000 属预期外用法 |
| `/api/*` 502 | `docker compose ps` 看 app 是否 healthy；app 首次启动建索引需 1–2 分钟 |
| 审批恢复 403 | 预期行为：仅 HR/ADMIN 可审批，且审批人不能是申请人本人 |
| compose 报 env_file 缺失 | 根目录必须有 `.env`（`cp .env.sample .env`） |

## 7. Kubernetes 部署（企业化第三阶段）

`k8s/` 目录提供 plain manifests + kustomize 编排（不引入 helm），与 compose
拓扑一一对应：postgres（StatefulSet + headless Service + PVC）、app
（Deployment 2 副本 + Service）、web（Deployment + Service + Ingress 示例）、
migrate Job（alembic 迁移 + 种子，幂等）、ConfigMap（非敏感配置）+
Secret 模板（敏感项）。

### 7.1 首次部署顺序

```bash
# 0. 构建并推送镜像（tag 同步改到 k8s/app.yaml / web.yaml / migrate-job.yaml）
docker build -t <registry>/hr-agent-app:<tag> .
docker build -t <registry>/hr-agent-web:<tag> ./web
docker push <registry>/hr-agent-app:<tag> && docker push <registry>/hr-agent-web:<tag>

# 1. 密钥：复制 k8s/secret.example.yaml 为 secret.yaml 填真实值（不入库），
#    或按下节接入 ESO / sealed-secrets
kubectl apply -f k8s/secret.yaml

# 2. 一键部署（kustomize；secret 由第 1 步先行创建）
kubectl apply -k k8s/

# 3. 等 postgres Ready 后跑初始化 Job（alembic upgrade head + database.seed，幂等）
kubectl -n hr-agent wait --for=condition=ready pod -l app=postgres --timeout=180s
kubectl -n hr-agent apply -f k8s/migrate-job.yaml
kubectl -n hr-agent wait --for=condition=complete job/hr-agent-migrate --timeout=300s

# 4. 验收
kubectl -n hr-agent port-forward svc/app 8000:8000
curl http://localhost:8000/health
```

### 7.2 关键口径与差异（vs compose）

- **探针**：app readiness/liveness 打 `GET /health`（零依赖轻量端点）；
  local 推理后端首次启动加载 BGE 权重较慢，readiness 给了 30s×6 宽限。
- **模型权重**：默认挂 `models-pvc`（RWX，多副本共享；无 RWX provisioner 改
  RWO + 单副本）。首次部署前用临时 Pod 跑 `python download_model.py` 灌入；
  或切 `EMBEDDING_BACKEND=tei` 卸载推理（Pod 不再需要模型卷）。
- **迁移**：不走 initContainer/启动钩子，独立 Job（`migrate-job.yaml`）显式
  执行，失败可重跑（alembic 与 seed 均幂等）。
- **HPA**：`app.yaml` 尾部有注释示例（CPU 口径）；local 推理后端内存敏感，
  生产建议先切 tei 再开自动扩缩。

### 7.3 密钥管理（Vault / ESO / sealed-secrets 接入指引）

`k8s/secret.example.yaml` 只是模板，**禁止提交真实密钥**。生产三选一：

1. **External Secrets Operator（推荐）**：集群装 ESO 后，建 `SecretStore`
   （指向 Vault / 云厂商 KMS），再建 `ExternalSecret` 把外部 path 映射到
   同名 `hr-agent-secret` 的各个 key。应用清单（`envFrom: secretRef:
   hr-agent-secret`）**零改动**——ESO 负责同步出同名 Secret。
   建议把 ExternalSecret 清单放 `k8s/overlays/prod/` 另建 kustomization 管理，
   不混进 base。
2. **sealed-secrets**：装 Bitnami sealed-secrets 控制器，用 `kubeseal` 把
   secret.yaml 加密为 `SealedSecret` 提交入库（可安全进 git），控制器在
   集群内解密为同名 Secret。同样清单零改动。
3. **手工 kubectl apply**：仅适合小规模/临时环境；secret.yaml 务必加进
   本地忽略，不提交。

CI 侧：`docker-check` job 已集成 kubeconform（钉版容器 `-strict`）静态校验
`k8s/` 全部清单，schema 不合规即阻塞。
