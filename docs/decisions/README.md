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

During Phase 01, prefer evidence gathering and implementation over accumulating architecture decisions. API discoveries belong under `docs/research/phase-01/` until verified and promoted into the phase specification.
