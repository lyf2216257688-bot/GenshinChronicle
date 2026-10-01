# P05-W2 Semantic Extraction Inventory Prompt Ablation (2026-09-30)

## Scope

This work unit adds an opt-in provider-facing Prompt/task formulation
experiment. It does not call a provider, rebuild any semantic root, modify the
compiler/input, change the authoritative schema or source-binding policy, or
change production/default behavior.

## Experiment contract

The new revision is phase05-w2-b-json-object-0.4 with Prompt version
phase05-w2-b-json-object-prompt-0.4. It clones the accepted v3 local
contract and changes only the task wording and extraction rules:

- inventory every independently stated, source-supported navigation record in
  each supplied stage;
- emit distinct material claims or changes instead of a representative small
  set;
- treat covered or one valid item as insufficient evidence of completeness;
- omit only unsupported or genuinely ambiguous material, with the existing
  segment disposition.

The request contract, authoritative output schema, compiler input, frozen
semantic-build/preflight identities, strict source binding, local-reference
validation, provenance, and fail-closed replay behavior remain unchanged.
Existing base, v2, and v3 identities are preserved.

## Verification

The v4 contract is accepted by the existing runner and CLI revision mapping.
Its zero-network preflight and offline replay pass with zero formal attempts,
provider calls, and network calls. Focused semantic prompt and B revision
regressions pass (17/17 across the two suites). The later corrected true-point1
comparison is recorded separately in `p05-w2-true-point1-v3-v4-20260930.md`.
It showed substantive recall improvement with redundancy, poetic-content
over-extraction and polarity-boundary costs. V4 remains opt-in and is not
production/default. No semantic build or active Navi/KG eligibility follows
from the live result.

## Superseded live boundary

The original three-unit live plan was executed only after separate
authorization, but its `507825` row was discovered to contain point2. That
historical root remains immutable and is not semantic evidence for point1. The
corrected true-point1 live root is separate; earlier failed/interrupted roots
remain mechanical execution evidence only. No retry, self-review, stronger-
model fallback, threshold, risk score, or new production schema was introduced.
The next direction is a Prompt-only simplification experiment that preserves
recall while reducing redundancy.
