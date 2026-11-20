FROM ghcr.io/astral-sh/uv:0.13.9 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /srv/evorag/backend
ENV PATH="/srv/evorag/backend/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
ARG EVORAG_ENABLE_RAGAS=false
RUN if [ "$EVORAG_ENABLE_RAGAS" = true ]; then \
        uv sync --locked --no-dev --no-cache --extra tracking --extra ragas; \
    else \
        uv sync --locked --no-dev --no-cache --extra tracking; \
    fi
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
COPY benchmarks ./benchmarks
RUN useradd --system --create-home evorag && install -d -o evorag -g evorag /data
USER evorag
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0"]
