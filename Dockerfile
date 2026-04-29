ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install system dependencies for database drivers
RUN apk add --no-cache \
    gcc \
    python3-dev \
    musl-dev \
    libffi-dev \
    mariadb-connector-c-dev \
    postgresql-dev

# Create virtual environment
RUN python3 -m venv /opt/ha-ops-mcp

# Copy project source — build context is the repo root
COPY pyproject.toml README.md /tmp/ha-ops-mcp/
COPY src/ /tmp/ha-ops-mcp/src/

# Install into the venv
RUN /opt/ha-ops-mcp/bin/pip install --no-cache-dir /tmp/ha-ops-mcp && \
    rm -rf /tmp/ha-ops-mcp

# Copy run script into the s6 service directory
COPY run.sh /etc/services.d/ha-ops-mcp/run
RUN chmod a+x /etc/services.d/ha-ops-mcp/run
