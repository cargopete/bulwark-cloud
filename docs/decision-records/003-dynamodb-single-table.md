# ADR 003: DynamoDB single-table design

**Date**: 2026-05-27
**Status**: Accepted

## Decision

Use a single DynamoDB table (`bulwark-cloud-state`) with a composite key design for all
entity types: Jobs, PassProgress, Findings.

## Alternatives considered

**Multi-table**: One table per entity type (jobs, passes, findings).

**RDS PostgreSQL**: Relational database with FK relationships.

## Rationale

- Single-table DynamoDB enables all primary access patterns in a single query by co-locating
  related items (all items for a job share `PK=JOB#{id}`).
- On-demand billing means zero cost when idle — appropriate for low-to-medium audit volume.
- DynamoDB provides PITR at 1-minute granularity; RDS would add ~$50+/month for a managed
  instance with comparable durability.
- The data model is append-mostly and has no complex relational queries (no JOINs required).

## Trade-offs accepted

- Single-table design is less intuitive than multi-table for newcomers. Mitigated by this ADR
  and clear item type annotations in the DynamoDB schema.
- Full-text search across findings is not possible in DynamoDB; addressed in v0.2 with
  OpenSearch Serverless if needed.
