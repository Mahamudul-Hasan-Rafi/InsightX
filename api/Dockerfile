# api/Dockerfile
#
# Multi-stage build — keeps build tools out of the final image.
#
# Stage 1 (builder): installs Python deps into an isolated virtualenv.
#   Needs gcc/g++ to compile pyodbc from source.
#
# Stage 2 (runtime): copies the venv and app code.
#   Installs only the Microsoft MSSQL ODBC 18 runtime (no build toolchain).

# ── Stage 1: build ──────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout=120 --retries=5 -r requirements.txt


# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm

# Microsoft MSSQL ODBC Driver 18 (required by pyodbc at runtime).
# unixodbc is the runtime ODBC library (not -dev, which is for compilation).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg unixodbc \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
       | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/microsoft-prod.gpg] \
       https://packages.microsoft.com/debian/12/prod bookworm main" \
       > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/*

# Copy virtualenv from builder
COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

WORKDIR /app
COPY . .

# Run as non-root
RUN useradd -m -u 1000 appuser \
    && mkdir -p /app/secure-uploads \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8091

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8091"]
