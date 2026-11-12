FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-web.txt requirements-core-web.txt ./
RUN pip install --no-cache-dir -r requirements-core-web.txt && useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
COPY backend backend
COPY core core
COPY migrations migrations
COPY *.py ./
COPY alembic.ini ./
USER app
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn backend.app:create_app --factory --host 0.0.0.0 --port 8000"]
