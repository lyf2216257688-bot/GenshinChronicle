# P04-W7 Unit3B Exposure Authority Amendment v1

> Historical checkpoint / current relation: this amendment remains the
> auditable authority for the completed W7 Unit3B lineage only. W7/Frozen48
> does not govern the post-W7 First End-to-End RAG path or its immediate next
> gate; those are owned by `docs/current-phase.md`.

## Purpose and scope

This addendum records the post-incident authority for Unit3B candidate-body
exposure ordering and the sanctioned application boundary. Within an explicitly
re-authorized future run of the historical W7 lineage, it governs Unit3B repair,
re-authorization, and retry only. It does not change semantic,
quality, C1, feedback, retry-limit, queue, quota, selection, or scientific
outcome criteria. It does not itself authorize or restart W7.

## Status and effectiveness

- Decision status: **TECHNICAL-LEAD APPROVED**.
- Repository authority status: **EFFECTIVE / FROZEN at accepted checkpoint
  `2f78e85`** (the first checkpoint committing this addendum).

This document is not repository-effective before that checkpoint and its actual
technical-lead diff review.

## Historical non-retroactivity

The failed production execution was governed by repository baseline
`a2a673ab5c7ff20dd4dfa0def5462d695fb2c5c3`. That snapshot remains the auditable
historical authority and is not rewritten by this addendum. This authority is
non-retroactive.

## Exposure readiness and terminality

For any explicitly re-authorized future run of this historical W7 lineage, the
following exposure rule is mandatory:

`candidate N full lifecycle terminal -> candidate N+1 body may be exposed`

All terminality, exposure, and fail-closed requirements below are binding only
for that explicitly re-authorized run.

Readiness is derived only from validated persisted state. `TERMINAL` is an
exposure-readiness/lifecycle state, not a scientific outcome, selection status,
quota rule, or final classification.

### Semantic REJECT

A candidate is exposure-terminal only after its valid semantic `REJECT` state is
immutably persisted under the existing Unit3 rules. No query attempt, quality
result, restricted C1 audit, or C1 author-feedback may exist for that candidate.

### Semantic ACCEPT

Semantic `ACCEPT` persistence alone is never exposure-terminal. The existing
maximum of two persisted authored attempts and sequence remain unchanged:

`author -> persist immutable attempt -> quality/pair-consistency -> restricted C1 only if quality PASS`

- Attempt 1 quality `REJECT`: no C1 or feedback. If the persisted quality
  outcome satisfies the existing frozen material-quality-failure retry trigger,
  it is not terminal and the candidate must complete the authorized Attempt 2
  lifecycle. If it does not satisfy that trigger, no Attempt 2 is authorized
  and the candidate is terminal. This addendum does not define materiality or
  the retry predicate. No authority-established, mechanically consumable
  persisted representation for that predicate was located; its implementation
  mapping is **UNKNOWN / BLOCKED FOR LATER REPAIR GATE**.
- Attempt 1 quality `PASS`: exactly one matching restricted C1 audit and its
  exact matching author-safe feedback must be persisted. Matching feedback with
  C1 `PASS` is terminal; matching feedback with C1 `REJECT` is not terminal and
  authorizes Attempt 2. Attempt 2 cannot begin before that feedback.
- Attempt 2 quality `REJECT`: no C1 or feedback; retry budget exhausted;
  terminal.
- Attempt 2 quality `PASS`: exactly one matching C1 audit followed by matching
  author-safe feedback; C1 `PASS` or `REJECT` is terminal only after feedback.

No Attempt 3 exists. The exact ACCEPT terminal states are therefore:

1. Attempt 1 quality `REJECT` persisted that does not satisfy the existing
   frozen material-quality-failure retry trigger;
2. Attempt 1 quality `PASS` + C1 `PASS` + matching feedback;
3. Attempt 2 quality `REJECT` persisted;
4. Attempt 2 quality `PASS` + C1 `PASS` or `REJECT` + matching feedback.

## Sanctioned application exposure boundary

If the historical W7 lineage is explicitly re-authorized, the sanctioned
semantic-facing path must derive the current candidate from validated persisted
state and expose exactly one authorized body. It must refuse future candidates,
caller-selected arbitrary candidate/index requests, and
batch/enumeration/multi-candidate requests. Semantic-facing code must not receive
a full-pack `records` interface, full-pack store capability, or pack iterator.

## Trusted executor and W7 non-goals

The approved W7 threat model is:

`trusted blind executor + application-level mechanical fail-closed exposure gate`

OS/filesystem/shell/sandbox confidentiality against a malicious executor is
not required and is not claimed. The trusted executor is procedurally forbidden
from directly reading, decompressing, or enumerating the production pack;
such a bypass invalidates the run and is a blinding/engineering `BLOCKED`
condition. W7 does not introduce ACLs, containers, capability-security
frameworks, or equivalent sandbox infrastructure.

## Violation and BLOCKED behavior

If the historical W7 lineage is explicitly re-authorized, unauthorized exposure
through that sanctioned path, including a future candidate, caller-selected
index, multi-candidate request, or invalid/ambiguous/tampered persisted state,
must mechanically fail closed.
Direct pack access that bypasses
the sanctioned path is not claimed to be mechanically prevented or detected at
the OS/filesystem level; if observed, it invalidates the run and is `BLOCKED`
under the trusted-executor procedure. Identity/configuration drift is also
`BLOCKED`; no semantic result is attributable to a blocked run.

## Repair acceptance requirements

If W7 repair is explicitly re-authorized, it must preserve B-0 persistence/write-order behavior and add synthetic
regressions proving: current candidate allowed; future candidate refused;
caller-selected candidate/index refused; batch/enumeration refused; exact
post-transition advancement; and deterministic resume. Each negative test must
isolate one exposure invariant and fail for that intended reason.

## Future ZERO-EXPOSURE preflight

Before any explicitly re-authorized W7 retry, mechanically verify: authorized source/checkpoint identity;
accepted A-2 manifest/pack identity and binding; authorized fresh/resume runtime
state; approved exposure-controller implementation/configuration identity; and
persisted position/readiness consistency. Proof that the trusted executor lacks
ambient shell/filesystem capability is not a W7 preflight requirement.
