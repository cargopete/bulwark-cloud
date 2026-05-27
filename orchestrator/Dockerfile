# Python orchestration layer on top of the Bulwark CLI base image.
#
# In CI the base image is built locally from github.com/cargopete/bulwark
# before this stage runs (see .github/workflows/deploy-dev.yml), so no
# Docker Hub pull is required.
#
# Build args:
#   BASE_IMAGE  — pre-built bulwark base (default matches IMAGE_TAG in CDK)
ARG BASE_IMAGE=cargopete/bulwark-cli:v0.1.0
FROM ${BASE_IMAGE}

LABEL component="bulwark-cloud-worker"
LABEL version="0.1.0"

# Install orchestrator deps as root; base image Python 3 is system-wide
USER root

WORKDIR /app

COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r /app/requirements.txt

COPY src/ /app/src/

# PYTHONPATH so the orchestrator package is importable without pip install
ENV PYTHONPATH="/app/src"

# Run as non-root — base image creates 'auditor' user
USER auditor

ENTRYPOINT ["python3", "-m", "bulwark_cloud_orchestrator.entrypoint"]
