FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PROJECT_DIR=/app
ENV MIGRATION_LOG_DIR=/tmp
ENV LOCAL_CACHE_DIR=/tmp/dvrt-cache
ENV HOME=/tmp
ENV SQL_ODBC_DRIVER="ODBC Driver 18 for SQL Server"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        unixodbc \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc -o /tmp/microsoft.asc \
    && gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg /tmp/microsoft.asc \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list \
        -o /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && useradd --no-create-home --home-dir /app --shell /usr/sbin/nologin appuser \
    && mkdir -p /tmp/dvrt-cache \
    && chown -R appuser:appuser /app /tmp/dvrt-cache \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/microsoft.asc

COPY --chown=appuser:appuser requirements.txt .
RUN python -m pip install --upgrade pip \
    && pip install -r requirements.txt

COPY --chown=appuser:appuser *.py ./

RUN python -m compileall -q .

USER appuser

CMD ["sh", "-c", "exec python -u plot_DVRT_HydroServer.py --serve --host 0.0.0.0 --port ${PORT:-8080}"]

CMD ["sh", "-c", "exec python -u plot_DVRT_HydroServer.py --serve --host 0.0.0.0 --port ${PORT:-8080}"] 0.0.0.0 --port ${PORT:-8080}"]
