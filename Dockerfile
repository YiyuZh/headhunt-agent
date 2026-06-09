FROM python:3.11-slim AS runtime

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_DEFAULT_TIMEOUT=120
ARG PIP_RETRIES=5

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_DEFAULT_TIMEOUT=${PIP_DEFAULT_TIMEOUT} \
    PIP_RETRIES=${PIP_RETRIES}

WORKDIR /app

RUN groupadd --system lietou \
    && useradd --system --gid lietou --home-dir /app --shell /usr/sbin/nologin lietou

COPY pyproject.toml README.md alembic.ini ./
COPY app ./app
COPY migrations ./migrations
COPY docs/agent-sops ./docs/agent-sops
COPY scripts/docker-entrypoint.sh /usr/local/bin/lietou-docker-entrypoint

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install -e . \
    && chmod +x /usr/local/bin/lietou-docker-entrypoint \
    && chown -R lietou:lietou /app

USER lietou

EXPOSE 8000

ENTRYPOINT ["lietou-docker-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
