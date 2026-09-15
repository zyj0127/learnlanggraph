# 企业 HR 智能助理 —— 生产可用容器化
# ----------------------------------------------------------------------------
# 构建（在项目根目录）：
#   docker build -t hr-agent .
#
# 运行（BGE 模型权重不打进镜像，挂载本机模型目录；DeepSeek 凭据经 --env-file 注入）：
#   docker run -d --name hr-agent -p 8000:8000 \
#     --env-file .env \
#     -v "C:\Users\<你>\.cache\modelscope\hub\models\BAAI\bge-small-zh-v1.5:/models/bge-small-zh-v1.5:ro" \
#     -v "C:\Users\<你>\.cache\modelscope\hub\models\BAAI\bge-reranker-base:/models/bge-reranker-base:ro" \
#     hr-agent
#
# 验收：
#   curl http://localhost:8000/health            # -> {"status":"ok"}
#   http://localhost:8000/docs                   # FastAPI Swagger UI
# ----------------------------------------------------------------------------
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 先装依赖再拷代码，充分利用 Docker 层缓存（代码改动不触发重装）
# pip 走清华镜像（容器内读不到宿主机 pip.ini）
COPY requirements.txt .
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

# 应用代码与知识库
COPY config.py logging_config.py observability.py telemetry.py streamlit_app.py ./
COPY agent/ agent/
COPY api/ api/
COPY database/ database/
COPY tools/ tools/
COPY data/company_handbook.md data/company_handbook.md

# Embedding / Rerank 权重路径：容器内统一挂载到 /models（只读），
# 覆盖 .env 里指向宿主机 Windows 路径的 EMBEDDING_MODEL / RERANK_MODEL
ENV EMBEDDING_MODEL=/models/bge-small-zh-v1.5 \
    RERANK_MODEL=/models/bge-reranker-base

# DeepSeek 凭据（DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL_NAME_CHAT）
# 不打进镜像，运行时经 --env-file .env 或 -e 注入
EXPOSE 8000

# 健康检查：slim 镜像无 curl，用标准库直连 /health
# start-period 放宽到 120s：首次启动需加载 BGE 权重并构建内存向量库
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

# db/employees.db 是种子固定的确定性 mock 数据，不入镜像、首次启动自动生成，
# 保证与评测集（eval/dataset.py 引用 build_roster()）严格同源
CMD ["sh", "-c", "mkdir -p db && ([ -f db/employees.db ] || python -c 'from database.mock_db import init_db; init_db()') && uvicorn api.server:app --host 0.0.0.0 --port 8000"]
