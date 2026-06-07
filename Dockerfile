ARG BUILD_FROM
FROM ${BUILD_FROM}

# Base image is HA's base-debian (Debian + s6-overlay + bashio); it ships no
# Python, so we install it via apt. Debian gives us official Playwright +
# Chromium support (Alpine/musl is unsupported by Playwright) — the reason for
# the base swap. See docs/KNOWN_GOOD_ENV.md and the v0.4x changelog.
#
# Size discipline (Supervisor builds this ON the HA host):
#   - chromium-headless-shell only, NOT full Chromium — we only screenshot /
#     profile headless; the shell is ~1/3 the size.
#   - no compiler toolchain: every runtime dep resolves to an aarch64/amd64
#     wheel on Debian (asyncpg, aiomysql, sqlalchemy, ...). If a future dep
#     needs building, prefer a multi-stage builder over bloating this image.

# Playwright stores its browser here (deterministic, independent of $HOME so
# the s6 service finds it at runtime regardless of the service env).
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python3 -m venv /opt/ha-ops-mcp

# Browser stack FIRST — it doesn't depend on our source, so a code change won't
# invalidate this layer and re-download Chromium (keeps rebuilds fast).
# `--with-deps` apt-installs the headless shell's system libs (needs apt lists,
# so refresh here and clean afterwards).
RUN /opt/ha-ops-mcp/bin/pip install --no-cache-dir playwright \
    && apt-get update \
    && /opt/ha-ops-mcp/bin/playwright install --with-deps chromium-headless-shell \
    && rm -rf /var/lib/apt/lists/* /root/.cache/pip

# Copy project source — build context is the repo root
COPY pyproject.toml README.md /tmp/ha-ops-mcp/
COPY src/ /tmp/ha-ops-mcp/src/

# Install the project (wheels only — fail loudly if a dep needs a build rather
# than silently dragging in a compiler).
RUN /opt/ha-ops-mcp/bin/pip install --no-cache-dir --only-binary=:all: /tmp/ha-ops-mcp \
    && rm -rf /tmp/ha-ops-mcp /root/.cache/pip

# Copy run script into the s6 service directory
COPY run.sh /etc/services.d/ha-ops-mcp/run
RUN chmod a+x /etc/services.d/ha-ops-mcp/run
