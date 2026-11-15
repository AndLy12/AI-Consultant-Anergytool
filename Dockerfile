# Node нужен только для сборки. В конечном Python-образе dev-сервер Vite не запускается.
FROM node:22-bookworm-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
# Зависимости устанавливаются внутрь Linux-образа, локальная .venv не используется.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-web.txt requirements-core-web.txt ./
# Отдельный слой зависимостей переиспользуется, пока requirements-web.txt не меняется.
RUN pip install --no-cache-dir -r requirements-core-web.txt && useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
COPY backend backend
COPY core core
COPY migrations migrations
COPY *.py ./
COPY alembic.ini ./
COPY --from=frontend-build /frontend/dist /app/frontend-dist
ENV FRONTEND_DIST=/app/frontend-dist
USER app
# Приложение работает без root; /data принадлежит ему и будет подключено как volume.
EXPOSE 8000
# После миграций exec заменяет оболочку сервером: SIGTERM от Docker доходит до Uvicorn.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn backend.app:create_app --factory --host 0.0.0.0 --port 8000"]
