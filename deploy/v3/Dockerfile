# 前端阶段只产出静态文件，最终镜像不携带 Node 构建环境。
FROM node:24-alpine@sha256:8a32c8cf649a49915a66e81e7a609223c708d0adabf288ee3f4077d765542f85 AS frontend
WORKDIR /build
# 先复制依赖清单复用安装缓存，再复制变化更频繁的前端源码。
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# 运行阶段使用锁定的后端依赖，并以非 root 用户提供 API 与页面。
FROM python:3.11-slim@sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60
WORKDIR /app
COPY backend/requirements-v3.lock ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd -m -u 10001 rag
COPY backend/app ./app
# 后端通过 V3_FRONTEND_DIR 查找同一镜像内的前端构建产物。
COPY --from=frontend /build/dist /app/frontend
ENV V3_FRONTEND_DIR=/app/frontend PYTHONUNBUFFERED=1
USER rag
EXPOSE 18000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18000"]
