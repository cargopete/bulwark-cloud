# ADR 001: ECS Fargate vs AWS Batch

**Date**: 2026-05-27
**Status**: Accepted

## Decision

Use ECS Fargate (via Step Functions `.sync` integration) for audit task execution.

## Alternatives considered

**AWS Batch**: Managed job queues, spot fleet management, job priority.

## Rationale

- AWS Batch shines at very long-running compute jobs (hours) with complex queuing requirements.
  Bulwark audits run ~45 minutes — within Fargate's comfortable operating range.
- Step Functions + ECS provides first-class observability, timeout handling, and retry semantics
  without an additional layer of abstraction.
- Batch compute environments add cost even when idle; Fargate has zero fixed cost.

## Revisit condition

If audits grow to regularly exceed 2 hours, or if we need job priority queuing across dozens of
concurrent audits, Batch becomes worth the operational overhead.
