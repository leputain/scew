FROM python:3.14-slim@sha256:a7fb1e634c4a578f9e0bd6327f11a3cde11b7a9395f48e24360c0988bcc5c2bc

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG PIP_NO_INDEX=0
ARG APP_VERSION=2026.08.11-p2.1
ARG BUILD_DATE=unknown
ARG VCS_REF=unversioned

LABEL org.opencontainers.image.title="SCREW attendance bot" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}"

ENV http_proxy=${HTTP_PROXY} \
    https_proxy=${HTTPS_PROXY} \
    HTTP_PROXY=${HTTP_PROXY} \
    HTTPS_PROXY=${HTTPS_PROXY} \
    SCREW_APP_VERSION=${APP_VERSION} \
    SCREW_BUILD_DATE=${BUILD_DATE} \
    SCREW_VCS_REF=${VCS_REF}

WORKDIR /app

COPY requirements.txt requirements.lock.txt ./
COPY wheelhouse/ ./wheelhouse/
RUN if [ "$PIP_NO_INDEX" = "1" ]; then \
        pip install --no-cache-dir --no-index --find-links=/app/wheelhouse -r requirements.lock.txt; \
    else \
        pip install --no-cache-dir --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org -r requirements.lock.txt; \
    fi

ENV http_proxy= \
    https_proxy= \
    HTTP_PROXY= \
    HTTPS_PROXY=

COPY core/ ./core/
COPY apps/ ./apps/
COPY scripts/ ./scripts/

ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Moscow

CMD ["python3", "apps/attendance_bot/app/main.py"]
