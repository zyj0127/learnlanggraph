# HR 助理 Web 前端 —— 多阶段构建：node 构建 dist → nginx 托管
# ----------------------------------------------------------------------------
# 构建（在 web/ 目录，或由根目录 docker-compose.yml 的 web 服务触发）：
#   docker build -t hr-agent-web ./web
#
# 构建参数（ARG 会注入为 Vite 构建期环境变量，编译进静态产物）：
#   VITE_API_BASE  默认 /api —— 生产经 nginx 反代到 app:8000，保持 /api 即可；
#                  仅在需要直连外部后端时才覆盖为完整 URL
#   VITE_MOCK      默认 false —— Mock 演示模式开关，生产必须为 false
# 示例：docker build --build-arg VITE_MOCK=false -t hr-agent-web ./web
#
# 离线/弱网注意：
#   - 依赖安装走 npm ci（严格按 package-lock.json），可用 NPM_REGISTRY 构建参数
#     切换镜像源：--build-arg NPM_REGISTRY=https://registry.npmmirror.com
#   - node_modules 与 dist 不入构建上下文（见 web/.dockerignore）
# ----------------------------------------------------------------------------
FROM node:20-alpine AS build

ARG NPM_REGISTRY=https://registry.npmjs.org
ARG VITE_API_BASE=/api
ARG VITE_MOCK=false

WORKDIR /build

# 依赖层：先拷 lockfile，源码变更不触发重装
COPY package.json package-lock.json ./
RUN npm ci --registry=${NPM_REGISTRY}

# 源码层：Vite 构建期读取 VITE_* 环境变量
COPY index.html vite.config.ts tsconfig.json ./
COPY src/ src/
ENV VITE_API_BASE=${VITE_API_BASE} \
    VITE_MOCK=${VITE_MOCK}
RUN npm run build

# ---- 运行层：nginx:alpine 托管静态产物 + 反代 /api ----
FROM nginx:alpine

# SPA 配置：路由 fallback、/api 反代（含 SSE 关键配置）、缓存与安全头
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html

EXPOSE 80

# 容器健康检查：alpine 自带 wget
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1
