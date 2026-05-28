# Python orchestration layer on top of the Bulwark CLI base image.
#
# Build context: repo root (bulwark-cloud/)
# The base image is built from github.com/cargopete/bulwark.
#
# In CI the base image is built from the bulwark repo before this stage runs.
# Locally you can pre-build it:
#   docker build -t bulwark-base:local /path/to/bulwark
#   docker build --build-arg BASE_IMAGE=bulwark-base:local -f orchestrator/Dockerfile .
#
# Build args:
#   BASE_IMAGE — pre-built bulwark base image URI
ARG BASE_IMAGE=cargopete/bulwark-cli:latest
FROM ${BASE_IMAGE}

LABEL component="bulwark-cloud-worker"
LABEL version="0.1.0"

# Install orchestrator deps as root; base image Python 3 is system-wide
USER root

WORKDIR /app

# Install pinned runtime deps
COPY orchestrator/requirements.txt /app/
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt

# Install the shared Pydantic models package
COPY shared/ /tmp/shared/
RUN pip3 install --no-cache-dir --break-system-packages /tmp/shared/

# Copy orchestrator source
COPY orchestrator/src/ /app/src/

# PYTHONPATH so the orchestrator package is importable without pip install
ENV PYTHONPATH="/app/src"

# Run as non-root — base image creates 'auditor' user
USER auditor

ENTRYPOINT ["python3", "-m", "bulwark_cloud_orchestrator.entrypoint"]
