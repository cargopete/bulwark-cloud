"""FastAPI application for the bulwark-cloud public API."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import audits, findings, health, reports

app = FastAPI(
    title="bulwark-cloud",
    version="0.1.0",
    description="AI-augmented smart-contract auditing as a service",
    docs_url=None,   # Disabled in prod; enable locally via STAGE=dev
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["Content-Type", "x-api-key", "Authorization"],
)

app.include_router(health.router)
app.include_router(audits.router)
app.include_router(findings.router)
app.include_router(reports.router)
