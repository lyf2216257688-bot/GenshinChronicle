# P04-W7 Unit3B Material-Quality Retry Authority Addendum v1

> Historical checkpoint / current relation: this addendum remains the
> auditable authority for the completed W7 Unit3B lineage only. Its retry and
> implementation-gate wording does not govern the post-W7 First End-to-End RAG
> path; current status and authorization are owned by
> `docs/current-phase.md`.

## Purpose and scope

This addendum closes the post-incident authority gap for classifying an
otherwise-valid persisted Attempt 1 quality/pair-consistency `REJECT`. Within
an explicitly re-authorized future run of the historical W7 lineage, it governs
Unit3B repair, re-authorization, retry, and exposure only. It
does not change semantic criteria, quality criteria, C1 criteria, feedback
projection, retry limit, queue rules, quotas, selection, or scientific
outcomes.

## Status and effectiveness

- Decision status: **TECHNICAL-LEAD APPROVED**.
- Repository authority status: **EFFECTIVE / FROZEN at accepted checkpoint
  `46fb3aa`** (the first checkpoint committing this addendum).

This historical addendum became repository-effective and frozen at accepted
checkpoint `46fb3aa`, after the technical-lead documentation review.

## Historical non-retroactivity

This addendum does not rewrite the frozen authority that governed the failed
execution. It could apply only to future Unit3B repair, re-authorization, and
retry after explicit re-authorization of the historical W7 lineage and after
becoming repository-effective.

## Normative material-quality retry rule

For an otherwise-valid persisted Attempt 1 quality/pair-consistency `REJECT`,
the disposition is retry-eligible `material quality failure` if and only if the
issue can be corrected solely by authoring Attempt 2 while preserving the
frozen target proposition, gold/review partition, candidate pair, candidate,
and queue.

If correction requires changing any frozen input, Attempt 2 is not authorized;
the valid quality `REJECT` is a terminal scientific outcome. If query-only
repairability cannot be affirmatively established by the authorized
quality/pair-consistency judgment, Attempt 2 is not authorized and the valid
quality `REJECT` is terminal.

Mechanical, integrity, or authority defects remain Unit-wide `BLOCKED`; they
are never scientific quality outcomes. C1-`REJECT` retry authority is
unchanged. Attempt 2 quality `REJECT` remains terminal, and no Attempt 3 exists.

## Authority, binding, and mechanical consumption

Material-quality retry disposition is an output of the authorized
quality/pair-consistency judgment for the exact persisted Attempt 1. The query
author/caller must not supply, choose, or override it. Persistence and
controller code must not independently decide repairability.

The disposition remains bound to the exact persisted Attempt 1 and the existing
authoritative frozen candidate/semantic-input identity and integrity bindings.
No second identity scheme is introduced here. Mechanical consumption means
carrying and consuming that already-authorized disposition; it does not derive
repairability from query text, free-text reasons, reason-code heuristics,
`next_action()`, implementation branches, or convenience logic.

Once wired, a missing, malformed, or ambiguous disposition must fail closed and
must never default to retry. The committed schema currently provides no such
authority-established persisted representation or field; its implementation
and wiring remain **NOT IMPLEMENTED / BLOCKED PENDING IMPLEMENTATION GATE**.

## Distinct outcomes

1. Retry-eligible material-quality `REJECT` → Attempt 2 authorized.
2. Valid non-material or indeterminate quality `REJECT` → terminal scientific
   outcome.
3. Mechanical, integrity, or authority defect → Unit-wide `BLOCKED`.

## Future implementation gate

If W7 repair is explicitly re-authorized, it may add only the minimum persisted representation and wiring
needed to carry the authorized disposition through the sanctioned workflow,
reuse existing identity/integrity validation, and fail closed on absent or
ambiguous state. It must not invent new scientific criteria or infer
materiality from implementation behavior.
