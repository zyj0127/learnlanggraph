# 企业 HR 智能助理 —— 生产可用容器化
# ----------------------------------------------------------------------------
# 构建（在项目根目录）：
#   python scripts/fetch_wheels.py     # 可选：预下载 Linux wheel，构建转为完全离线
#   docker build -t hr-agent .
#
# 运行（BGE 模型权重不打进镜像，挂载本机模型目录；DeepSeek 凭据经 --env-file 注入）：
#   docker run -d --name hr-agent -p 8000:8000 \
#     --env-file .env \
#     -e EMBEDDING_MODEL=/models/bge-small-zh-v1.5 \
#     -e RERANK_MODEL=/models/bge-reranker-base \
#     -v "E:\code\py\learnlanggraph\.local_models\BAAI\bge-small-zh-v1___5:/models/bge-small-zh-v1.5:ro" \
#     -v "C:\Users\<你>\.cache\modelscope\hub\models\BAAI\bge-reranker-base:/models/bge-reranker-base:ro" \
#     hr-agent
#
# 上面两个 -e 不是多余的：.env 里的 EMBEDDING_MODEL / RERANK_MODEL 指向宿主机 Windows
# 路径（本地直跑需要），而 docker run 的 -e / --env-file 优先级**高于**镜像内的 ENV ——
# 只用 --env-file 会把镜像里设好的 /models/... 覆盖掉，容器启动即报
#   OSError: Repo id must use alphanumeric chars ... : 'sentence-transformers/E:\code\...'
# 实测对照：仅 --env-file → 取到 Windows 路径；--env-file 与 -e 同时给 → -e 胜出。
# ----------------------------------------------------------------------------
# 验收：
#   curl http://localhost:8000/health     # -> {"status":"ok"}
#   http://localhost:8000/docs            # FastAPI Swagger UI
#
# 依赖来源（重要）：
#   torch 必须走 PyTorch 的 CPU 专用索引。Linux 平台 PyPI 上的 torch wheel 会连带
#   拉入 nvidia-cu12-* / triton 等数 GB 的 CUDA 运行时依赖 —— 纯 CPU 推理场景下
#   既拖垮构建时间，又让镜像虚胖一倍以上，还会在弱网下直接把 docker build 挂死。
#   注意 PEP 440 对本地版本号的排序是 2.13.0+cpu > 2.13.0，因此两个索引同时给出时
#   pip 会优先选中 CPU 版，无需额外固定版本号。
# ----------------------------------------------------------------------------
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# ---- 依赖层 ----
# 先装依赖再拷代码：改业务代码不会触发重装。
# BuildKit 内置 frontend 已支持 --mount，故不写 "# syntax=docker/dockerfile:1"
# （该指令会去 docker.io 拉 frontend 镜像，本机网络下拉不到会直接构建失败）。
#   type=cache : pip 的 HTTP 缓存在多次构建间复用，大包（torch ~180MB）中断后
#                无需从零重下 —— 这是本机弱网下能一次跑通的关键。
#   type=bind  : build_wheels/ 以只读方式挂入，离线安装用的 wheel 不进入镜像层。
#
# 两条路径都必须带 -c constraints.txt：镜像内的版本要跟评测基线同源，否则
# "容器里跑的就是评测过的那套"这句话不成立。实测漏带时会装成 langchain 1.4.0 /
# mcp 2.2.0 / sentence-transformers 6.0.1（mcp 直接跨了大版本），
# 而宿主机验证基线是 langchain 1.3.12 / mcp 1.28.1 / sentence-transformers 5.6.0。
COPY requirements.txt build_wheels/constraints.txt ./

RUN --mount=type=bind,source=build_wheels,target=/wheels,ro \
    --mount=type=cache,target=/root/.cache/pip \
    set -ex; \
    if ls /wheels/*.whl >/dev/null 2>&1; then \
        echo ">>> 离线安装：build_wheels/ 已备好 $(ls /wheels/*.whl | wc -l) 个 wheel"; \
        pip install --no-index --find-links=/wheels -c constraints.txt -r requirements.txt; \
    else \
        echo ">>> 在线安装：build_wheels/ 为空，回退国内镜像（torch 取 CPU 专用索引）"; \
        pip install -i https://mirrors.aliyun.com/pypi/simple/ \
            -c constraints.txt \
            --find-links https://mirrors.aliyun.com/pytorch-wheels/cpu/ \
            --timeout 120 --retries 10 "torch==2.13.0"; \
        pip install -i https://mirrors.aliyun.com/pypi/simple/ \
            -c constraints.txt \
            --timeout 120 --retries 10 -r requirements.txt; \
    fi

# ---- 应用代码与知识库 ----
COPY config.py logging_config.py observability.py telemetry.py streamlit_app.py ./
COPY agent/ agent/
COPY api/ api/
COPY database/ database/
COPY tools/ tools/
COPY data/company_handbook.md data/company_handbook.md

# Embedding / Rerank 权重路径默认值：容器内统一挂到 /models（只读挂载）。
# 注意这只是兜底 —— docker run 的 -e / --env-file 优先级高于此处。本项目 .env 里
# 也有同名变量且指向宿主 Windows 路径，因此 run 时必须用 -e 显式覆盖（见文件头示例），
# 否则容器会拿 Windows 绝对路径去当 HF repo id，启动即失败。
ENV EMBEDDING_MODEL=/models/bge-small-zh-v1.5 \
    RERANK_MODEL=/models/bge-reranker-base

# DeepSeek 凭据（DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL_NAME_CHAT）
# 不打进镜像，运行时经 --env-file .env 或 -e 注入
EXPOSE 8000

# 健康检查：slim 镜像无 curl，用标准库直连 /health
# start-period 放宽到 120s：首次启动需加载 BGE 权重并现场构建内存向量库
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

# db/employees.db 是种子固定的确定性 mock 数据，不入镜像、首次启动自动生成，
# 保证与评测集（eval/dataset.py 引用 build_roster()）严格同源
CMD ["sh", "-c", "mkdir -p db && ([ -f db/employees.db ] || python -c 'from database.mock_db import init_db; init_db()') && uvicorn api.server:app --host 0.0.0.0 --port 8000"]
