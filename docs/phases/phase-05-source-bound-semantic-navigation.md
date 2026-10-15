# Phase 05 — Source-Bound Semantic Navigation

Status: **active; P05-W1 mechanical contracts PASS; P05-W2-U1 provider-free
projection/accounting PASS; B JSON-object formal local-contract canaries PASS;
B v2 prompt/contract and v3 review-hardening provider-free validation PASS;
semantic-quality and real-story-slice acceptance OPEN**

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

## P05-Navi-E1 local exploration boundary

E1 is an opt-in, provider-free tool and evidence-wiring experiment, not an
Agent or a production selector. Local lexical search reuses existing scoring;
RU reads verify the bound Canonical manifest/record and the deterministic RU
projection. Context is limited to requested units and explicit same-component
ordinal neighbors. Dialogue graphs remain source structure, without inferred
speaker/branch semantics. Truncation and omitted ranges must remain visible;
partial receipts cannot receive E1 active review acceptance.

Recorded actions persist request identity, completed responses, read receipts,
unverified clues and hard round/search/read/candidate/context budgets. Context
counts the serialized Unicode characters of complete delivered tool responses,
including metadata. Recovery requires identical inputs, policy and decisions;
completed actions are not reissued. This local-only replay contract authorizes
no provider transport or provider recovery behavior.

Navigation groups are topic items only. Explicit human review binds the exact
group and completed checkpoint, records source qualification and narrative
caveats, and may result in `curated_recorded`. Missing reviews remain inactive.
An explicit mechanical-fixture opt-in permits `accepted_for_fixture_test`
only; it never substitutes for human semantic acceptance or production
eligibility. The real-source smoke uses this fixture route and produces a
separate pending human-review request.

Optional scoped RU/Assembly loading verifies the complete artifact and retains
all occurrences of selected records. Scope is recorded in the prepared-context
build manifest and exposed by the E1 wiring audit; it is not a field in the
formal Packet build projection and is not a new corpus identity. The navigation
RU index must include scoped page companions that unchanged Formal Deferred
Assembly may expose. The experiment reports baseline overlap, search hits, read
sources, Assembly input, formal admission dispositions and final Packet
visibility separately. It makes no relevance, model-autonomy or answer-
improvement claim.

## P05-Navi-E2 single-case experiment boundary

E2 uses the E1 local tools with model-selected search/read/clue/stop decisions.
This opt-in experiment is limited to one source-bound case, at most 6 model
rounds, 8 searches, 12 reads, 8 pages, 20,000 Canonical body characters,
and an authorized provider budget (up to 100,000 total provider tokens and
8,192 output tokens per round for new immutable Responses API executions; historical
runs remain frozen under their respective Chat Completions contracts: r1/r2 at 30,000 total
/ 2,048 output tokens, and single-action continuation r3/r4 at 30,000 total / 4,096 output tokens).
Serialized tool-response characters are accounted separately. Each dispatch
requires a conservative UTF-8-byte input reservation plus the output ceiling;
UNKNOWN usage blocks the next dispatch. The byte reservation is an upper-bound
guard, not a measured model tokenizer or a cost estimate. New runs communicate
via the Responses API; historical Chat Completions request forms remain
reconstructible for offline identity and audit verification.

Requests, wire identities, issued records, streamed responses, terminal usage,
decisions, actions and checkpoint snapshots are persisted. New E2 execution
requires both an explicit logical model and matching route. There is no default
model, cross-route/model fallback, or protocol fallback. SDK retries are disabled.
Application retry is limited to at most 3 attempts on the same route/model for
the exact evidence-backed, error-only, pre-generation TokenMetro TPM 429 rejection.
Each issued attempt consumes the call ceiling and has an immutable terminal;
retry reservation is bounded by remaining call/token budgets. Other errors stop.
An unresolved issued request is EXECUTION UNKNOWN and is never reissued. Historical
r1-r4 no-retry contracts and Chat Completions identities remain unchanged.
Completed local actions/decisions may be recovered. A pre-dispatch token
projection stop may continue in a new child root with the same case and
budgets, retaining parent identity, completed actions and cumulative usage;
it does not restart the budget. A separately user-authorized child may continue
from a resolved length/empty-visible terminal; it must rederive cumulative
usage/calls from immutable issued/terminal evidence, including failed requests.
The six-call ceiling includes those failed requests. One-short-action output
and a changed per-call output reservation are explicit child operating points,
not a total-budget reset or automatic retry. Any subsequent terminal failure
stops that continuation. Full receipts remain preserved when the
model-facing projection omits metadata or visibly truncates snippets.

All model clues and memory retain `unverified_hypothesis_not_fact`. No E2 result
creates an active view, KG edge, human semantic acceptance or production
acceptance. Reference oracle sources are withheld until post-run review, unless
independently returned by the allowed tools. E2 is exploration evidence only;
formal Phase04-versus-Navi Packet/answer comparison remains separately
reviewed and authorized.

### E2 multi-model Responses boundary (2026-10-15)

The E2 Responses seam supports only the explicit pairs `gpt-6-luna` /
`tokenmetro`, `glm-5.3-flash` / `jizhi_glm`, `qwen3.8-flash` /
`tokenmetro_qwen`, and `deepseek-v4.1-flash` / `jizhi`. The Qwen route has
an independent credential slot even though its confirmed Base URL shares
TokenMetro's hostname. The later operator-selected Jizhi GLM route also has an
independent `NAVI_GLM_API_KEY` / `NAVI_GLM_BASE_URL` slot; it cannot use the
DeepSeek Jizhi key or the historical GLM TokenMetro key. Historical route configs
remain frozen for offline audit. An explicit new operator authorization may
create a separate bounded diagnostic campaign while leaving prior UNKNOWN
requests unresolved and never resuming/reissuing them. Its immutable authorization
binds the verified prior campaign manifest and counts all prior issued calls
against the same total/per-model ceilings; UNKNOWN in the new campaign stops
that model. This exception does not authorize autonomous research recovery. Runtime Base URLs must match the confirmed route;
credentials are runtime environment material, never identities or artifacts.
Archived semantic SDK live dispatch (including automatic TokenMetro-to-Jizhi
fallback) is retired; those runner paths accept offline MockTransport only.
Frozen request reconstruction, hashing and read-only replay remain available.
No wire alias is guessed. Returned model identity, Responses terminal status,
visible text, provider reasoning events, usage and E2 decision validity are
recorded separately. Missing/inconsistent usage, empty text, invalid actions,
failed/incomplete terminals and indeterminate execution stop fail closed.
Provider reasoning summaries are identified as summaries; absent reasoning is
NOT_PROVIDED, not fabricated private reasoning. All models share E2 prompts,
actions, memory, source binding, budgets and checkpoint rules.

New batch and single-action continuation prompts direct reads through the exact
formal `unit_id` supplied by seed/search; both normal and compact candidate
projections retain that ID. `model_round` counts model requests and
`action_index` identifies a global tool action; neither is a read source ID.
The executor still requires seed/search admission and verified RU/Canonical
binding within the unchanged read/page/character budgets. Legacy `hit.round`
remains a zero-based global action offset (not a model round), paired with a
zero-based candidate `index`; its parser and rejection of invalid/non-search
coordinates remain unchanged for historical compatibility. New prompts and
requests receive new immutable identities; frozen requests are not regenerated.

A protocol probe uses a small synthetic fixture and validates ordered
search/read/clue/stop output through the E2 validator plus fixture reference
checks. It executes no tools, reads no Canonical corpus, and establishes no
autonomous research or semantic quality acceptance. Its campaign contract fixes
3 issued requests per logical model / 12 overall, counts retries and unresolved
requests, and stops the affected channel on execution uncertainty. Successful
HTTP authentication, Responses protocol and E2 action protocol are separate
acceptance gates. Current authorization/results belong to current-phase.md and
the E2 experiment report; E2 remains PARTIAL and E3 requires separate authorization.

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

## P05-W2 review hardening (provider-free)

The opt-in experiment `phase05-w2-b-json-object-0.3` is a new prompt/experiment
identity over the unchanged JSON output schema. Prompt identity,
`2c332577f91edb50806a4f4c2cf663b720932c4833ac0e6023e1050c74370a2d`, adds an
explicit stage completeness check for dates, subject headings, point markers,
and other source stage boundaries. The compiler fragments explicit `主题：`
and dialogue point boundaries before provider input construction and retains
the parent segment lineage under source-unit-policy-0.2; unmarked long text
still follows the existing hard-cap/oversized handling. The prompt also
requires minimal source binding.
An image/map-only `map_desc` may support a directly stated location/topic item,
but it may not be attached redundantly to a textual event, fact, relation, or
mention. v3 provider-free preflight and replay pass with zero formal/provider/
network calls. This is a contract and omission-risk repair, not semantic
acceptance or a model benchmark.

The opt-in experiment `phase05-w2-b-json-object-0.4` introduces prompt version
`phase05-w2-b-json-object-prompt-0.4`. It defines a bounded stage inventory task
instructing the extractor to build a complete inventory of every independently
stated, source-supported record across topics, mentions, facts, events, and
relations while preserving material changes.

The opt-in experiment `phase05-w2-b-json-object-0.5` introduces prompt version
`phase05-w2-b-json-object-prompt-0.5`. It bounds the inventory to independently
navigable claims, scanning every supplied stage completely to extract concrete
topics, identities, goals, state changes, events, explicit relationships, and
epistemically qualified facts, avoiding fragmenting every descriptive or poetic
clause into a standalone item while maintaining complete stage review recall.
Both v4 and v5 operate under the unchanged output schema and
`local_strict_json_schema_source_binding_minimality` acceptance authority.

### Navi E2 exploration checkpoint status

At the historical `main@b5ffbb6` checkpoint, autonomous P05-Navi-E2
exploration was paused and checkpointed. E1 mechanical tool wiring passed
completely; E2 cumulative issued 5 calls (4 terminals, 1 unresolved recorded as
`EXECUTION UNKNOWN` with reissue prohibited). This frozen accounting does not
include later synthetic protocol probes. Present authorization and immediate
next action are owned exclusively by `docs/current-phase.md`.

## Historical TokenMetro SDK retry and resume contract

The following pre-2026-10-15 execution contract is retained for immutable
identity, offline fixtures and read-only replay only; it cannot authorize new
live execution. New execution follows the E2 Responses contract above.

Historical Phase 05 TokenMetro Chat Completions execution used the official
`openai` Python SDK with SDK automatic retries disabled. The direct-HTTP route
required explicit opt-in for live invocation through both CLI and programmatic
APIs. Its real network opener is now retired even with that former opt-in;
injected fixture openers, offline request construction, parsing, and replay remain available.
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

This section describes the retained sequential SDK benchmark runner
`run_sdk_units`. Its transient retry/resume policy is not the semantic-batch
single-route policy below, nor the route-pair policy.

## Immutable semantic-batch rate control

This retained coordinator/ledger contract supports offline audit and fixtures.
Its archived SDK live bridge is retired; the policies below are not new E2
execution selectors and do not override the explicit Responses route contract.

The semantic-batch coordinator retains separate workers (1, 6, or 12) and a
minimum dispatch interval. `--min-dispatch-interval-seconds` is finite and
nonnegative; its default is 0 (no proactive pacing). The thread-safe gate runs
at the SDK request hook before issued evidence and transport dispatch. It
controls new request starts, including retries, while previously dispatched
requests may remain concurrently in flight. It does not serialize generation.
Explicit custom executors remain a one-provider-attempt callable boundary;
the coordinator gates their invocation conservatively. They must not perform
hidden additional provider calls or retries.

Automatic retry defaults to zero. The only eligible error is the observed
TokenMetro pre-generation TPM rejection: HTTP 429, SDK `RateLimitError`, a
persisted error-only JSON envelope with exact
`error.code` or `error.type=RateLimitExceeded.EndpointTPMExceeded`, no unknown
error fields; the optional `error.param` is absent, `null`, or the observed
empty string only; and zero stream chunks, visible/reasoning characters,
usage, or finish reason. Any other 429, generation evidence, timeout, connection/read
failure, partial stream, or ambiguous execution remains `execution_unknown`
and cannot authorize automatic retry. The exact pre-generation policy 403
remains a terminal failure in this single-route path and never authorizes a
route change here. This predicate is client evidence for execution certainty;
provider billing remains UNKNOWN when usage/cost evidence is absent.

`--rate-limit-max-retries` defaults to 0 and permits 0 through 4 retries per
logical unit within this root. An eligible rejection honors a finite
`Retry-After` number or HTTP date within the configured cap; missing/invalid
headers use bounded exponential backoff. The configurable defaults are
`--rate-limit-backoff-initial-seconds=1`,
`--rate-limit-backoff-multiplier=2` (at least 1), and
`--rate-limit-backoff-cap-seconds=30`. Timing values must be finite and
nonnegative. A qualifying rejection schedules a shared cooldown for all new
dispatches; its retry also passes through the global pacing gate. Exhaustion
stops new dispatches and produces `blocked`, preserving the terminal rejection
and draining already issued attempts. These numbers are adjustable operating
points, not channel capacity claims or permanent speed limits.

The gate atomically reserves one request-budget slot for each provider attempt,
including every retry. Thus 429, 429, success uses three calls and three
immutable attempt directories. SDK automatic retries remain disabled.
Reservations before a local dispatch/persistence failure are conservative:
they cannot allow an extra call, while issued evidence remains the attempt
accounting authority. The summary exposes both issued attempts and reservations.
Wall-clock includes pacing/backoff and local preparation; expiry prevents new
dispatches but does not interrupt an issued request's safe persistence/drain.
Ctrl+C stops dispatches and interruptible waits, drains issued attempts, writes
the structured interrupted summary, and retains CLI exit code 130.

The existing immutable manifest identity continues to bind route, API surface,
model, prompt, schema, source and frozen units. At first execution, the root
freezes a write-once `execution_config.json` using the same canonical JSON hash
mechanism, bound to that manifest identity. It binds workers, pacing, complete
retry policy/predicate version, request/max-unit/wall-clock budgets,
stop-on-unknown and SDK output ceiling/timeout. Progress, dispatch, issued and
terminal evidence bind its execution identity. An executed root is never reused;
an explicit continuation/rerun child may freeze a different operating point.
Ordinary resume selects only never-issued units; retry exhaustion, an unknown
attempt or a terminal rejection needs an explicit rerun child. Pre-dispatch
request artifacts without issued or terminal evidence do not override earlier
terminal state or consume provider attempts.

Each attempt preserves request/wire/response/stream evidence plus dispatch
ordinal/timing and an immutable retry decision with eligibility, reason, delay,
delay source and scheduled/exhausted disposition. Interrupted/budget-stopped
retries also retain `retry_stopped.json`. Progress and error indexes expose
per-attempt and final unit states. Offline audit verifies identity, hashes,
safe retry predicates, retry ordering/delay, dispatch ordinals/pacing and
budget/accounting consistency without provider calls. Historical roots lacking
the new execution artifacts remain readable under their original contract.

## Historical DeepSeek route-pair contract

This pre-2026-10-15 contract is retained for offline request reconstruction,
fixture testing and replay only. Live route-pair dispatch and all automatic
cross-provider fallback are retired for new execution.

The historical production candidate route was TokenMetro with Jizhi as an explicit,
conservative fallback for the same `deepseek-v4.1-flash` model. Route profiles
are loaded at runtime from `TOKENMETRO_BASE_URL`/`TOKENMETRO_API_KEY` and
`JIZHI_BASE_URL`/`JIZHI_API_KEY`; only route names, safe configuration
identities, and environment variable names may enter persisted evidence.

The route-aware SDK runner creates one logical request identity over source,
Prompt v2, schema, model, canonical messages/payload, stream setting, output
ceiling, and generation parameters. A fallback attempt must use the same
identity and is represented as a second immutable attempt under the same unit.
The wire request is Chat Completions on both routes for this contract; a
Responses dialect requires a separate contract and is not inferred from
historical relay evidence.

Fallback is legal only for TokenMetro HTTP 403 with exact JSON
`error.code=content_policy_violation`, or the frozen TokenMetro variant
`error.type=content_policy_violation` with null `error.code`, received before
any stream chunk or visible/reasoning content, with no usage or finish reason.
Timeout, connection
errors, 429/5xx, ordinary 403, incomplete streams, partial content, output
length, and local parse/schema/source-binding failures are never route
fallbacks. They preserve their terminal evidence and stop fail closed.

Route-pair execution uses no automatic retry. Primary terminal evidence is
persisted and checkpointed before a fallback request is issued. Accepted units
are skipped on resume; unresolved attempts, changed identities, or a prior
fallback terminal block further requests. Accounting exposes route history,
policy-403 counts, fallback outcomes, execution-unknown stops, latency, and
provider-reported usage/cost with `UNKNOWN` preserved where unavailable.

Completed stream evidence is durably appended and fsynced as JSONL while the
provider stream is active. After a complete terminal stream, the runner
atomically writes deterministic gzip JSONL beside the attempt, records both
compressed and uncompressed hashes/byte counts plus the row count and format,
then removes the loose JSONL. An archive failure leaves the attempt without a
terminal and therefore unresolved for resume, preserving fail-closed accounting.
Offline audit accepts both `jsonl-1` and `jsonl-gzip-1`; gzip is an artifact
storage representation, not a canonical semantic representation. The 200-unit
measurement retained all 2,213,505 stream rows while reducing 881,535,308
stream bytes to 10,314,280 in a temporary projection; file count remained
unchanged. Full-build loose-file/index cost remains outside this contract and
requires a future measured storage decision.

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
