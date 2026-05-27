# ADR 002: Step Functions vs EventBridge Pipes for workflow orchestration

**Date**: 2026-05-27
**Status**: Accepted

## Decision

Use AWS Step Functions for audit workflow orchestration.

## Alternatives considered

**EventBridge Pipes + SQS**: Event-driven pipeline via queues and pipes.

**Lambda orchestration**: API Lambda directly manages the whole workflow.

## Rationale

- Step Functions provides explicit state, built-in retry semantics, structured error handling,
  and a visual execution trace — all of which are essential for debugging 45-minute audits.
- EventBridge Pipes suit stream-processing workloads, not long-running sequential jobs.
- Lambda orchestration would require reinventing retry logic, timeout management, and execution
  history — all of which Step Functions provides for free.

## Trade-offs accepted

- Step Functions costs $0.025 per 1000 state transitions. At ~10 transitions per audit, this is
  $0.00025 per audit — negligible.
- State machine definition adds some CDK verbosity vs. simple Lambda chains.
