# Phase 05 — Source-Bound Semantic Navigation

Status: **active; P05-W1 mechanical contracts PASS; P05-W2-U1 provider-free
projection/accounting PASS; B JSON-object formal local-contract canaries PASS;
B v2 prompt/contract provider-free validation PASS; semantic-quality and
real-story-slice acceptance OPEN**

This specification is the durable Phase 05 boundary. Current authorization,
production/default behavior, and immediate next action remain owned by
`docs/current-phase.md`.

## Purpose

Phase 04 established the first complete end-to-end RAG baseline. Its Block A
retrieval/ranking behavior, accepted identities, Evidence Packet authority, and
Generation-visible contract remain frozen. The valid targeted 9Q evidence
showed mechanical execution PASS but `0/9` materially improved final answers;
Block B v0.1 production/default adoption remains blocked. Phase 05 addresses
the remaining navigation, admission visibility, and multi-hop story-reasoning
requirements without treating Block B v0.1 as a semantic success.

## Semantic build boundary

Phase 05 consumes Canonical-first inputs plus a verified Retrieval Unit locator
map. It produces a versioned, rebuildable semantic build lineage and two
distinct derived views: cross-record hierarchy navigation and event/relation
graph traversal. A shared build lineage permits reuse and incremental rebuild;
it does not require one provider request per Canonical input. Staged extraction
is allowed when it is safer or more economical, provided stage identities,
cost, cache/reuse, recovery, and derived-view provenance remain explicit.
Semantic item collections are canonicalized by item identity for build
identity; semantic stage order remains part of lineage.

Semantic interpretation is not evidence authority. Raw, Parsed, Canonical,
Retrieval Unit lineage, and the final Evidence Packet retain their existing
authority boundaries.

## Source binding and semantic acceptance

`source_binding_status` and `semantic_acceptance_status` are separate. A
verified RU/Canonical/source binding proves only mechanical provenance binding;
it does not prove semantic correctness. Active view eligibility requires both a
verified source binding and an explicit fixture/curated acceptance status.

Unresolved, rejected, unsupported, ambiguous, or unaccepted output remains in
an auditable inactive ledger with input/run/raw-response binding and an
explicit reason. P05-W1 has no automated semantic judge and makes no production
semantic-acceptance decision.

An LLM quote is not provenance. Stored quotes/spans may be active only when
mechanically verified against an authoritative representation exposed by the
current RU/Canonical contract. The implementation must not invent byte/span
precision.

## Ownership

`retrieval/` owns semantic build/view materialization, source-binding checks,
rejection accounting, and the deterministic primitive that converts resolved
navigation RU candidates into input for unchanged Formal Deferred Assembly.

`rag/` owns online evidence assessment, the provider-neutral navigation
decision seam, route lineage, bounded traversal invocation, and calls into
retrieval capabilities. It does not duplicate candidate/admission/Assembly
algorithms.

`generation/` remains unchanged for P05-W1. Route, hop, and coverage traces
are audit/evaluation data and are not Generation-visible input.

## Navigation contract

The navigation decision seam is provider-neutral. P05-W1 uses deterministic
fixtures, fakes, or recorded decisions; no lexical seed policy or provider is
frozen as the durable architecture.

Hierarchy and graph traversal require hard hop, node, edge, and candidate
budgets, cycle/stagnation protection, and auditable termination. BFS, beam, or
another algorithm may be used as a first operating point but is not a durable
architecture invariant.

Navigation audit records distinguish baseline-overlap candidates, novel
resolved candidates, Assembly-admitted candidates, final-Packet-visible
candidates, and omitted candidates. Navigation completion requires at least
one novel navigation candidate to be visible in the rebuilt Packet; a
baseline-only Packet cannot be reported as navigation success.

If round-0 evidence is sufficient and the legal action is `answer_now`, the
existing baseline answer path is used without navigation. If navigation is
selected or required and fails, the failure is explicit. The result may only
follow an existing legal bounded-partial or stop disposition and can never be
an unqualified full answer.

## P05-W1 evidence boundary

P05-W1 proves only provider-free mechanical contracts for semantic build/view
materialization, source binding and explicit acceptance separation, inactive
rejection accounting, bounded navigation, official RU resolution,
admission/Packet visibility, identities, persistence/recovery, and unchanged
Generation integration.

It does not prove real-provider extraction quality, automatic semantic assertion
correctness, corpus-scale hierarchy/graph quality, final-answer improvement,
Q058 repair, or production/default adoption.

## P05-W2-U1 provider-free boundary

The first W2 implementation work unit is provider-free. It materializes a
Canonical-first provider-facing projection, source-meaningful compilation
units, a local provenance/RU-binding sidecar, explicit omission and oversized
ledgers, a deterministic bounded sample manifest, cap/fragmentation profiles,
and a provider-neutral semantic output envelope. The corrected accepted corpus
evidence is rooted at
`data/retrieval/p05-w2-u1-provider-free-20260922-r5` with semantic build
identity `45520b23490a1c66289476c71dbe55c38cd206167342ebeb5894721055c59677`;
the prior r3 root is retained as historical review evidence. The r5 sample
contains 42 projection members, 30 overlapping semantic-quality members, and
18 predeclared challenger-paired members (14 high-risk plus 4 matched
controls). Every selected compilation unit is RU-eligible across all of its
source segments; corpus-level RU-unbound accounting remains separate. The
provider-facing payload is 436,490,276 bytes / 347,552,660 chars after compacting
rich-text segment structure to paths and character counts without repeating
normalized lexical text. It verified the accepted Canonical manifest and RU
artifact with zero provider, API, network, or model calls.

Provider-facing input identity hashes only the serialized projection payload
plus projection/schema policy. Complete Canonical record SHA-256, lineage,
raw references, RU binding, and execution provenance remain local sidecar or
audit data. A local parser/validator change may reprocess a preserved immutable
raw provider response without another provider call; a provider request,
prompt, output-schema, or generation-config dependency change requires a new
request, and missing raw response fails closed. An unknown provider model
revision does not invalidate an accepted immutable artifact and does not prove
fresh-execution equivalence.

U1 is cost/identity evidence only. It does not execute the first
Gemini-versus-DeepSeek live comparison or establish semantic acceptance,
automatic escalation, hierarchy/graph quality claims, answer improvement, or
production adoption. Current execution authorization is owned by
`docs/current-phase.md`.

## P05-W2 live-preflight boundary

The immutable provider-free preflight is
`data/retrieval/p05-w2-live-preflight-20260923-r2`, identity
`8601fb2e665fd5f1bc1b7a9ef35a871269e3ea00f0b9a23654656a0bb3838da0`.
It freezes exactly 30 Gemini semantic-quality inputs and an 18-unit DeepSeek
subset composed of 14 high-risk units and 4 matched controls. The deterministic
r7 sample correction preserves the accepted U1 semantic build identity while
replacing two r5 controls whose compilation-unit content was not an ordinary
comparison and two r5 oversized units; no provider result informed the change.
Every frozen unit is fully RU-bound and no oversized, unresolved, or partial
input is admitted.

The prompt, output schema, provider execution configuration, request order,
payload identities and exact serialized input sizes are immutable preflight
artifacts. Automatic retry, escalation and sample expansion are disabled; the
absolute ceiling is 30 primary plus 18 challenger requests. Provider revisions
and provider token counts remain explicitly `UNKNOWN`. Preflight performs no
provider/API/network/model activity and does not execute the paid comparison;
current authorization and immediate next action are owned by
`docs/current-phase.md`.

## P05-W2 B JSON-object experiment boundary

The approved active structured-output direction is the independent experiment
revision `phase05-w2-b-json-object-0.1`, identity
`44a410016feec821f5ab80f40509deb94bb956f9d12331ad076e8c26227dc161`.
Its compact prompt identity is
`6e5f4140318937fff598c9d8a509891e95519883cd31779e855c70c2b2041a6d`;
its OpenAI-compatible Chat Completions request-contract identity is
`b802b77d802178525077af5ccd8250bbf88dc70b9e9bf27f5bba96e423993f2d`;
and it continues to bind authoritative semantic-output schema identity
`9848b1e07c09b64156497dddc7a1a244f6dd9d7c3d5455c8c0dd0fae380cf312`
and semantic build identity
`45520b23490a1c66289476c71dbe55c38cd206167342ebeb5894721055c59677`.
These identities are separate from runtime provider/model/config, semantic
input, frozen unit, run, and attempt identities. API credentials are never
identity or artifact material.

B requests `response_format={"type":"json_object"}`, `stream=false`, and the
existing deterministic temperature setting. It does not send or depend on
provider-enforced strict JSON Schema. Its acceptance authority is exclusively
local and fail closed: preserve the raw provider envelope and transport
metadata, extract the expected assistant content, parse the whole content as
one JSON object without fence stripping, substring recovery, or repair,
validate the authoritative output schema, then validate exact supplied-segment
coverage and item binding. Raw response evidence is durable before any of
those fallible local steps and supports deterministic provider-free replay.

`accepted_for_local_contract` proves only syntactic JSON, authoritative schema,
and source/segment binding. It does not prove semantic correctness, human or
curated acceptance, active semantic-view eligibility, answer improvement,
production adoption, or Phase 05 completion. Any extraction, parse, schema, or
binding error is `rejected_fail_closed`; a sent request remains consumed and is
not silently retried. The old strict JSON-Schema experiment remains frozen
historical A evidence and is neither reused as B identity nor rewritten.

The provider-free B preflight uses a synthetic response and a permanently
offline adapter to exercise request construction, raw persistence, the full
local validation chain, and deterministic replay. It consumes no formal unit
or ordinal and reports zero provider and network calls. A future paid canary
requires separate authorization, an explicit unattempted frozen unit, a unique
output root, historical attempt accounting, writable raw persistence, and the
preflight PASS; it is limited to one request with no retry or fallback.

## P05-W2 B v2 extraction prompt boundary

The provider-neutral extraction prompt v2 is
`phase05-w2-b-json-object-prompt-0.2`, identity
`25ebc483c5dfcfe9b3dc8ddd01001f75e98ff4c2b5327aa789a3af84a068003e`.
Its separate experiment revision is `phase05-w2-b-json-object-0.2`, identity
`50f7219556771d8698bc5f85f464bd58d2bdd84c5bbea748178ad45db7255e98`.
The authoritative output schema identity remains
`9848b1e07c09b64156497dddc7a1a244f6dd9d7c3d5455c8c0dd0fae380cf312`;
the JSON-object request-contract identity remains
`b802b77d802178525077af5ccd8250bbf88dc70b9e9bf27f5bba96e423993f2d`.
The old B prompt and experiment retain their historical identities and artifacts.

V2 asks for distinct material event turns and explicit relationships rather
than a paragraph-level story summary. It preserves participants, source voice,
tentative claims, and negation in existing fields. Every item has a response-local
`local_id`; event participants resolve to local mention IDs, while relation
endpoints resolve to local mention or event IDs. Equal labels across compilation
units do not establish identity. The prompt forbids unsupported causal,
inverse, transitive, equivalence, and generic `related_to` links. Each item
binds to the smallest sufficient set of supplied source segments. Segment
coverage accounts for processed input, not complete extraction of its important
meaning. The v2 local-reference review is an additional provider-free contract
check; it does not alter the B output schema or promote semantic acceptance.

Provider-free validation reused the immutable ordinal 16, 20, and 21 requests
without modifying their historical responses. Source-anchored review fixtures
show that the unchanged schema can represent the diary's missing event turns
and reversal and the investigator's attributed tentative relation. This proves
task and schema expressibility only, not future model behavior. Offline request,
schema/binding, local-reference, and replay checks passed for Gemini, DeepSeek,
and GLM configurations with zero provider/network calls and zero formal attempts;
reports are under `.local/p05-w2-b-v2-contract-validation-20260924-r1`.
The frozen Gemini/DeepSeek preflight is not a three-model paid comparison
contract. Current paid authorization remains governed by `docs/current-phase.md`.

The later bounded SDK benchmark is a distinct request operating point under
the same frozen Prompt v2 and output schema. It uses the official OpenAI Python
SDK with only `model`, frozen `system`/`user` messages, and a common
`max_tokens` ceiling in the request body; it does not claim the historical B
JSON-object request-contract identity. Local strict JSON, authoritative schema,
source binding, and v2 local-reference checks remain fail-closed authority.
The new benchmark identity binds transport, request shape, output ceiling,
models, frozen inputs, and validator; its run and attempt ledgers remain
separate from historical B attempts. Current results and authorization are
owned by `docs/current-phase.md` and its linked research note.

## TokenMetro SDK retry and resume contract

Formal Phase 05 TokenMetro Chat Completions execution uses the official
`openai` Python SDK with SDK automatic retries disabled. The direct-HTTP route
requires explicit opt-in for live invocation through both CLI and programmatic
APIs. Its offline request construction, parsing, and replay remain available.
A logical compilation unit may have several provider attempts;
the attempt number, request identity, latency, HTTP/SDK terminal, provider
request ID when available, usage, retry classification, sanitized request,
raw response or error, local validation, and canonical output are preserved
per attempt. A later success never overwrites a failed attempt.

The initial operating point is one request plus at most two configurable
retries per invocation. Configuration above four retries fails closed before
issuing a request. Only HTTP 429, 500, 502, 503, 504 and timeout or
connection failures are transient. Retry delay honors `Retry-After` within a
configured cap, otherwise bounded backoff applies. HTTP 401/403/404,
`finish_reason=length`, malformed/truncated output, strict JSON/schema,
local-reference, and source-binding failures are not transport retries.
`length` is an output-budget block. Exhausted transient retries pause the
model before subsequent units; a later invocation may resume the same frozen
work after the provider recovers.

Resume verifies the frozen semantic input, prompt, schema, model, request
shape, SDK/runtime dependencies, validator, and persisted artifact hashes.
Accepted units are skipped. Only pending or terminal transient units may be
requested again. An issued attempt without an unambiguous terminal record,
tampered artifact, or changed identity fails closed; it cannot be silently
charged or incorporated into the same run. The retry count and bounded delay
are recorded per invocation and attempt, but can change on resume without
changing the frozen semantic/request identity. They are operating points,
not permanent correctness invariants. Local-contract acceptance
remains separate from human semantic acceptance and active-view eligibility.

## P05-W1 acceptance

The provider-free contract fixture demonstrates two deterministic 2-hop routes
and one deterministic 3-hop route resolving to fixture RU/Canonical-shaped
provenance. A real bounded story slice must additionally satisfy the same flow
using accepted local Genshin Canonical/Retrieval Unit evidence.
Active items must carry verified source bindings and explicit fixture/curated
acceptance. Invalid or unaccepted items must remain inactive. A sufficient
round-0 Packet must answer without navigation; required navigation failure must
remain explicit and legal; final Packet citations must resolve only through
official RU-backed evidence; route traces must not enter Generation input; and
provider/network call count must remain zero.

The checked-in fixture exercises the provider-free contract and is intentionally
synthetic. It must not be represented as a real Genshin story-slice result.
Real-story-slice acceptance remains open until a bounded slice can be selected
from accepted local Canonical/Retrieval Unit evidence with exact provenance and
without guessed semantic links or provider activity. It is an acceptance
dependency for the later authorized semantic/live evaluation and eventual full
Phase 05 closure, not a new gate added by W1.

## Persistence and recovery boundary

W1 provides bounded, auditable persistence/recovery primitives: versioned
stage/build identities, exact reused-item IDs, partial/failure status, artifact
hashes, reload/tamper checks, and overwrite refusal. W1 does not introduce a
generic resumable workflow; any later resume implementation must validate its
source/build/stage dependencies against these persisted identities.

## UNKNOWN classification

Non-blocking UNKNOWNs and first-version operating points are the later
provider/model choice, one-request versus staged extraction, initial hierarchy
expansion policy, traversal algorithm, hop/node/edge/candidate limits,
large-record strategy outside this slice, full-corpus cost/performance,
whether semantic navigation improves answer quality, and whether Q058 later
needs Generation work. These can be measured after W1 and do not block its
mechanical contracts.

Decision UNKNOWNs are limited to choices that would create a materially
different durable architecture and require approval before dependent work.
Blocking UNKNOWNs are limited to missing identity, provenance, correctness
authority, safety, or other formal-contract facts that make continuing W1
require guessing. No such UNKNOWN is open for the approved W1 slice.
