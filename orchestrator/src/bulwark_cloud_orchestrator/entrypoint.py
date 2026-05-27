"""bulwark-cloud worker entry point.

Runs inside the ECS Fargate task. Reads job parameters from environment variables
set by Step Functions, invokes the bulwark CLI as a subprocess, streams structured
logs to CloudWatch, uploads artefacts to S3, and updates DynamoDB job state.

Exit codes:
  0       success
  1-9     job-level failure (target won't compile, unreachable repo, etc.) — no retry
  10-19   transient infrastructure failure (S3, DynamoDB, Anthropic API) — Step Functions retries
  20-29   terminal infrastructure failure — no retry, ops alert
"""

from __future__ import annotations

import sys

import structlog

from .audit_runner import AuditRunner
from .config import JobEnv

log = structlog.get_logger()


def main() -> int:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
    )

    try:
        env = JobEnv.from_env()
    except Exception as exc:
        log.error("failed_to_parse_env", error=str(exc))
        return 20

    structlog.contextvars.bind_contextvars(job_id=env.job_id)
    log.info("worker_starting", model=env.model, repo=env.target_repo)

    runner = AuditRunner(env)
    exit_code = runner.run()

    log.info("worker_done", exit_code=exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
