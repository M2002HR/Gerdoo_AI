FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120

WORKDIR /app

# Optional build-time networking/proxy/index settings (passed via docker-compose build args).
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG ALL_PROXY=""
ARG NO_PROXY=""
ARG PIP_INDEX_URL=""
ARG PIP_EXTRA_INDEX_URL=""

COPY requirements.txt ./requirements.txt
RUN set -eux; \
    if [ -n "${PIP_INDEX_URL}" ]; then pip config set global.index-url "${PIP_INDEX_URL}"; fi; \
    if [ -n "${PIP_EXTRA_INDEX_URL}" ]; then pip config set global.extra-index-url "${PIP_EXTRA_INDEX_URL}"; fi; \
    HTTP_PROXY="${HTTP_PROXY}" HTTPS_PROXY="${HTTPS_PROXY}" ALL_PROXY="${ALL_PROXY}" NO_PROXY="${NO_PROXY}" \
    pip install --upgrade --no-cache-dir pip setuptools wheel; \
    HTTP_PROXY="${HTTP_PROXY}" HTTPS_PROXY="${HTTPS_PROXY}" ALL_PROXY="${ALL_PROXY}" NO_PROXY="${NO_PROXY}" \
    pip install --no-cache-dir -r requirements.txt

COPY . /app

ENV PYTHONPATH=/app/src

CMD ["python", "-m", "gerdoo_ai_bot.main"]
