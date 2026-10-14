# Architecture Decision Records

This directory is for **new, durable engineering decisions** that materially constrain future implementation.

Do not import the entire historical discussion or hundreds of old DEC notes into Codex's normal working context. The repository should carry only the decisions that remain useful as current engineering constraints.

## When to create an ADR

Create one when a decision:
- has meaningful alternatives;
- is expensive to reverse;
- affects multiple phases/components;
- establishes a durable invariant.

Do not create ADRs for ordinary implementation details, temporary research findings, or parameters that should remain configurable.

## Suggested format

```markdown
# ADR-NNN — Short title

Status: proposed | accepted | superseded
Date: YYYY-MM-DD

## Context
...

## Decision
...

## Consequences
...

## Evidence
...
```

## Current policy

Prefer evidence gathering and implementation over accumulating ADRs. API
discoveries and scoped experiment results belong in the applicable research
note until verified and promoted into a durable phase contract or an ADR when
the decision is costly or hard to reverse.

## ADR Index

| ADR | Title | Status | Date | Scope |
| :--- | :--- | :--- | :--- | :--- |
| [ADR-0001](adr-0001-obc-content-id-contract.md) | MiHoYo OBC `content_id` Stable Content Key Contract | accepted | 2026-09-15 | Phase 01 / Raw Acquisition |

