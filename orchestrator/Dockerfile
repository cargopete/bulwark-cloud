# Multi-stage: orchestration layer on top of the Bulwark CLI image.
# The bulwark binary and all its tooling (Slither, Foundry, Halmos, Claude Code,
# ~70 audit skills) are already in the base image. We add a thin Python wrapper only.

FROM cargopete/bulwark-cli:v0.1.0 AS bulwark

# ── Python orchestration layer ─────────────────────────────────────────────
FROM bulwark

LABEL component="bulwark-cloud-worker"
LABEL version="0.1.0"
LABEL description="ECS Fargate worker: Python wrapper + bulwark CLI"

# Install Python 3.12 (base image is Debian-based)
RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends python3.12 python3.12-venv python3-pip && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps before copying source so layer is cached
COPY requirements.txt /app/
RUN python3.12 -m pip install --no-cache-dir -r /app/requirements.txt

# Copy orchestrator source
COPY src/ /app/src/

# Run as the non-root bulwark user that the base image already provides
USER bulwark

# Entry point: Python wrapper; invoke Bulwark as a subprocess
ENTRYPOINT ["python3.12", "-m", "bulwark_cloud_orchestrator.entrypoint"]
