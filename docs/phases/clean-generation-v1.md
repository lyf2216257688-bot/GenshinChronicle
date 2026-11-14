# Clean-based Generation V1 contract

2026-11-14. Implementation follows the confirmed [Plan](../research/clean-generation-implementation-plan-20261114.md).
Current authorization and completion belong exclusively to [current-phase](../current-phase.md).
The Plan's original planning-only authorization describes its historical creation;
the operator subsequently authorized G0–G2, then G3 implementation and a bounded
paid capability test, then G4 offline engineering in an independent worktree.
Current scope and results remain owned by current-phase.

## Decisions and boundaries

All 24 decisions are retained, without a new architecture/model selection:

| Decisions | Binding contract |
| --- | --- |
| D01 | Local, personal, open-source application; no hosted service or accounts. |
| D02 | Adaptive allocation within explicit finite hard limits; quality acceptance is separate. |
| D03 | Analysis, hypotheses and counterevidence remain distinct from source facts and causality. |
| D04 | Follow user detail/structure requests; no mandatory modes or spoiler policy. |
| D05 | General knowledge can suggest hypotheses; no automatic web verification or invented citations. |
| D06 | Target consequential gaps; allow bounded conclusions and unresolved questions. |
| D07 | Expand reading within proven source handles, including complete natural Parents. |
| D08 | Retrieval discovers; Reranking orders; Generation validates selections and assembles Packets. |
| D09 | S0/F1_max; retain Sparse/S1/S2; no automatic routing or fusion tuning. |
| D10 | Preserve original question; optional rewrites/subquestions require recorded gaps and ancestry. |
| D11 | Necessary main-question research only; record incidental unresolved questions. |
| D12 | Query-local ranking, intermediate evidence retained, no cross-query logit comparison. |
| D13 | Explicit sufficient/limited/no-gain/budget/cancel/fault stop reasons; no false sufficiency. |
| D14 | LangGraph state/flow, GC retrieval/evidence/model_calls; reuse algorithms. |
| D15 | One controller, task roles using one model; no agent team. |
| D16 | TokenMetro/deepseek-v4.1-flash-1017 only, no route fallback. |
| D17 | Task-dependent thinking requires exact-route capability evidence. |
| D18 | Native Chat tools first; JSON protocol needs an explicit separate decision. |
| D19 | Preserve qualified partial evidence; source failures isolated, drafts never final. |
| D20 | SQLite same-chat persistence, independent new chats, explicit continue after restart. |
| D21 | Full history retained; deterministic context indexes preserve corrections, qualifiers and locators. |
| D22 | Persist ordered progress events; reasoning/control protocol hidden by default. |
| D23 | Engineering/source, semantic and continuous-research acceptance are separate. |
| D24 | Bounded incremental capability; no global/cross-chat research memory. |

Plan sections 3–7 and 9 supply the detailed confirmed interface, recovery,
capability, budget and semantic constraints. G4 remote operation requires a finite
campaign authorization; G5–G7 retain separate gates. Documenting an interface
does not authorize calls.

## Ownership, identities and storage

`rag/clean_*` owns the opt-in local orchestration skeleton. `model_call_sessions`
bridges GC `model_calls.run/Policy/Batch`; it is not a second sender or retry policy.
`retrieval/clean_based/*_v2` owns versioned queries, local workers and selected
S0/BGE ordering. `generation/clean_*` owns verified reads, fragment selection,
provider-neutral Packet composition and later new Chat adaptation. No seventh layer.

Default configurable root: `data/generation/clean-v1-local/`. `sessions.sqlite`
owns submissions, turns, cancellation and ordered events; `checkpoints.sqlite`
owns LangGraph progress. Immutable operations, reads, Packets and results are
files referenced by hash. `campaigns/<id>/` owns fixed budget configuration,
fingerprint mappings, shared Batch and call ledger. `locks/` holds OS locks for
application ownership, session execution and the single GPU. SQLite never
reconstructs a lost evidence file. Missing/corrupt seals block affected recovery.
Generated files, sessions, secrets and runtimes are Git-ignored.

Identity chain: session → stable submitted turn → logical operation → shared
attempt. A submit token is idempotent only for the same session and text.
Operation identity binds body fingerprint, campaign/root, turn, policy, schema
and code. Different turns may legitimately submit identical bodies. Recovery
must retain the original identity and budget root. No random fingerprint salt.

## G1 persistence and dispatch contract

Use isolated regular-GIL CPython 3.14.5; pin framework dependencies after a
staging compatibility fixture, then install the same lock in the new runtime.
Do not alter ML environments or frozen shared modules.

The graph checkpoints a prepared operation synchronously before dispatch.
Framework retries are absent. An application ownership lock serializes execution;
turns persist in submission order and session/GPU locks prevent competing clients.
Reopening displays saved state; only explicit continuation invokes an unfinished graph.

Reservation order is operation/fingerprint → campaign → Batch budget. Every
attempt enters persistent turn limits inside reserve, including retries. UNKNOWN
consumes its reservation. Cancellation and admission share a short control lock;
no new admission after cancellation, and in-flight work retains honest status.
No cancellation/budget-exhaustion summary model call is allowed. Reports retain
provider usage separately from conservative reservations and actual fees.

Recovery must cover every row of Plan §5.3: prepared first send; unsealed reserved
UNKNOWN; sealed attempt/call incomplete; pending authorized retry (reconcile
without sending); sealed call/checkpoint missing; sealed tool receipt/message
missing; missing/hash-changed file; incompatible code/schema. Replay verifies and
repairs derived budget and ledger outcomes from exact attempt/call seals before
returning. Repairs are idempotent and never rewrite original evidence.
No server exactly-once claim is made.

G1 dispatch is Mock-only. Live is disabled and missing finite limits reject live
requests. G3-Live requires a new explicit authorization and exact capability profile.

## G3 Chat protocol and capability contract

`generation/clean_chat.py` owns `gc-clean-chat-1` manifests and raw JSON/SSE
decoding. `clean_chat_transport.py` subclasses the unchanged G1 session bridge;
every attempt still passes through `model_calls.run`, `TurnBatch` and its fixed
campaign/turn budget. The urllib sender has no SDK retries, proxies or redirect
following. Default examples stay live-disabled. A separate exact-preparation,
code-bound, expiring permit is required before live admission. Missing credentials
fail before reservation. UNKNOWN and format retries are zero for initial probes.

Profiles bind exact endpoint/model, Chat surface, thinking toggle, stream,
tool-schema digest, prompt/protocol version and reasoning-history policy; the
request manifest also fixes whole-message/context/output/timeout limits. Profile
requests start `NOT_LIVE_VALIDATED`; observed capability records reference sealed
calls and declare their finite scope. Mock results never upgrade live capability.
No server schema enforcement, service idempotency, exact tokenizer or price is
inferred from a protocol PASS. There is no alternate JSON action protocol.

HTTP bytes are flushed/fsynced before parsing or draft events. Decode JSON and
SSE with exact response identity, one choice, explicit finish/usage and arithmetic
validation. SSE may split UTF-8 and tool arguments anywhere; accumulate by index
and call ID, reject identity changes, duplicate IDs, missing terminals and usage.
The full completed batch of calls must pass strict JSON and all local schema
constraints before any tool executes. Supported schemas form an explicit bounded
dialect; unknown keywords fail, never silently weaken validation. This dialect
supports closed nested objects, typed/enum leaves and bounded arrays.

Results distinguish `final_text`, `tool_calls`, `refusal`, `incomplete`, `invalid`
and `cancelled`. The historical shared state `CONTENT_VERIFIED` means only the
adapter's scoped protocol validation, never semantic acceptance. Only
`kind=final_text` supplies answer_text; draft events have `final=false`. Reasoning,
original assistant/tool arguments and draft content remain separate local files;
reasoning and control arguments are not progress events or source evidence.
Tool continuation preserves the sealed assistant message and reasoning verbatim,
plus exactly linked tool receipts. Reject orphan, repeated or incomplete tool
protocol units; receipt replay does not repeat the previous model request.

Cancellation blocks later admission and is checked again before sending/reading
and between serial tools. A transport interruption retains raw fragments and
UNKNOWN accounting. A sealed known result survives later cancellation/expiry and
can replay without network or credentials. Socket timeout and cooperative elapsed
checks bound reads/admission; they do not cancel already admitted remote effects.

`clean_probe.py` is an explicit finite synthetic capability workbench, with sealed
preparation, request previews, separate approval, partial results and per-profile
evidence. It is not a G4 chat or G5 research controller. Its live tests consume
only their approved root/budget, stop on failure and do not create standing API
permission. Real endpoint/model mismatches or unsupported capabilities require
an operator decision; do not silently normalize identities or change routes.

`clean_tools_probe.py` is a separate tools-first preparation/approval/run entry;
it preserves the original probe and its closed authorization. Capability profiles
are assessed independently: missing thinking evidence does not block separately
approved disabled-thinking tools or SSE tests. A complete identity/usage-valid
unexpected result can close one case without preventing the remaining predefined
independent cases. UNKNOWN, HTTP/protocol/identity failure, overrun, cancellation,
expiry or local evidence failure stops the batch. No failed case is retried.
Stream interruption checks on local copies are engineering evidence only, never
proof of an actual provider interruption's behavior, fee or service reliability.
Ordinary native tools, thinking tools and active research retain separate verdicts;
success of one profile never qualifies another or grants live integration permission.

## G2 query, ranking, source and Packet contract

ResearchQuery V2 binds session/turn/query, original question, current subquestion,
search_text/rerank_text, kind/round, parents, gap and combination. V1 original-only
validation stays unchanged. CandidateSet/RankedCandidateSet V2 retain complete
Chunk/source/hit/disposition information and explicit query/scoring digests.
Empty results are complete empty outputs and skip scoring. Partial/nonfinite,
foreign or overlength scores fail. Selected BGE remains FP16 CUDA/eager/batch1,
2048 paired tokens, no truncation, MMR off; errors never invoke fallback.

Reuse selected indexes, migration gate, fusion, coverage and ranking primitives.
CPU readers may remain open; Dense and Reranker never coexist on GPU. Worker
process trees are Job-owned and must exit before the next lease starts.
Frozen core/config/evidence hashes and existing experiments remain unchanged.

Only candidates and legally derived Parent handles authorize reads. Read receipts
carry exact structures/text, snapshot, CleanRef/Raw origins, conditions, speaker
and unknown authority. OBC admission/hash integrity does not prove official facts.
Fragments are program-generated IDs with source positions; selection uses IDs,
never model-supplied quotes/offsets. Required context and conditions accompany
selected text. Natural Parent receipts retain the complete structure, including
non-admitted values and dialogue graphs; display order never asserts chronology.

Assembly consumes complete ordered groups, validated read/selection requests and
an explicit whole-message budget. Preserve group order and within-group ranking;
no discovery, semantic reranking or cross-query score mixing. Record every
candidate/selection as included, covered_duplicate, not_selected, budget_excluded,
invalid_source or context_expansion. Deduplicate only proved same-observation,
same-context full coverage; partial overlaps and independent witnesses survive.
Required qualifiers that do not fit cause exclusion, not misleading truncation.
Identical inputs/policy produce identical Packet and citation mapping. Whole-message
budget includes system, question, conversation, tools/history, evidence and reserved
output/thinking. Unverified tokenizer uses explicitly conservative UTF-8 estimation,
never an exact vendor-token or price claim.

## G4 same-chat fixed-chain and local UI contract

`rag/clean_conversation.py` adds versioned local chat/job/message/note/draft
tables to the existing `sessions.sqlite`, using the unchanged G1 Store. Full
messages, typed research notes, context snapshots, ranked inputs, exact reads,
Parents, Packets and results are immutable files under `chat_artifacts/<turn>/`,
referenced by path/hash. `gc-clean-conversation-1` records session/turn/message
identity and provenance. Corrupt/missing referenced files block affected display
or recovery; DB text must not recreate them. On Windows the new store uses a
consistent extended absolute path spelling, including for long worktrees.

Draft text and submit tokens persist in SQLite. An unchanged submitted draft
reuses its turn on refresh/double click. Editing its text creates the next token;
explicitly repeating identical text creates a legitimate new turn. Metadata is
also immutable for a submitted token. Chat settings and campaign remain fixed.

`clean_context.py` builds a per-turn extractive index when the configured byte
allowance is exceeded. Complete local history is preserved. Earlier excerpts
retain message/citation pointers and omission counts; oldest index entries may
be omitted as whole records with the original message IDs retained in the audit.
Recent exchanges, active typed requirements/corrections, research notes and whole
validated tool protocol units are protected. If these cannot fit, fail explicitly.
This is deterministic organization with zero model calls, not semantic LLM
summarization. There is no automatic semantic extraction of research claims.
User assertions/hypotheses never become supported facts through history reuse.

Explicit intent/scope corrections take effect as requirements; plot assertions
and plot corrections remain unverified user material. Corrections invalidate
targeted premises and their dependency closure. An untargeted correction
conservatively invalidates earlier premises of that class. Derived context is
rebuilt from revisions; invalidated premises are not restated as current facts.
No automatic source adjudication or claim verification is inferred from Mock.

`clean_conversation_graph.py` uses sync LangGraph boundaries for context →
fixed original-query retrieval/ranking → read/Assembly → prepared Chat operation
→ Chat SSE answer → citation/source check → immutable result/message publication.
It reuses G2 V2 fusion/ordering and EvidenceReader/Assembly, and G3 ChatSessions
through the unchanged shared caller. Actual escaped Chat-message bytes plus
output reserve are checked; evidence admission accounts for envelope overhead.
Each final citation is addressed by turn + Packet + E-number, and Parent viewing
uses a saved source-bound receipt. Citation integrity is not semantic support.
Protocol drafts remain nonfinal; partial/fault/cancel outcomes preserve qualified
source material and do not turn streamed fragments into final answers.

`clean_executor.py` is one local background supervisor per application data root,
with an OS ownership lock and the existing application/session locks. It runs
one `clean_turn_worker.py` child at a time, reusing the Windows Job process-tree
owner; G3's process-wide offline socket guard cannot affect Streamlit's loopback
communication. The supervisor thread itself does not run model transport. UI writes
submissions, observes real saved events and requests cancel/continue. Jobs are
FIFO across chats; boot/refresh does not enqueue earlier work. Explicit continue
reuses the same operation/checkpoint/campaign. Shutdown blocks new admission,
retains in-flight facts and leaves unfinished work for explicit continuation.
User cancellation persists, prevents later dispatch and closes that turn;
subsequent research uses a new turn. A sealed known response survives cancellation.
UNKNOWN reservations are retained and never bypassed through a new operation.

The independent `ui/clean_chat_app.py` defaults to Mock, and
`rag/clean_conversation_cli.py` remains Mock-only. Tiny source fixtures and deterministic hits/scores/answers are
marked synthetic; real parsing, readback, Packet, protocol, persistence and
accounting paths are exercised. Mock execution has no credential read or live
dispatch. There is no research tool loop, global memory Store or cloud service.
Each remote profile needs matching exact-route evidence and fresh batch
authorization. Limited disabled-thinking tools/SSE observations do not qualify
thinking; missing thinking evidence does not block separately authorized ordinary
integration. Synthetic tool success does not validate actual GC tools or QA.
Create-only intents under `chat_submissions/` precede G1 turn publication, allowing
the turn/job/message gap to reconcile without losing correction metadata or
starting execution. `executor_runs/` retains worker PID/log/shutdown receipts.

The optional `requirements-py314-generation-ui.lock` extends the immutable G1
framework lock in the isolated regular-GIL 3.14.5 Generation environment. It must
resolve with the original pins; no ML runtime or existing lock is changed.

## G4 finite remote fixed chain

`rag/clean_live.py` owns finite campaign preparation/admission and a lazy wrapper
around unchanged G2 LocalPipeline. It specializes only G3 admission, not its
sender, retries or UNKNOWN recovery. The immutable permit binds data root, shared
campaign, limits, expiry, integration/adapter/upstream code, selected configuration
hashes and prior ordinary SSE evidence. Each new request is checked against the
exact verified context/Packet and a saved wire preview before reservation and send.
All chats share that campaign; creating another chat cannot replenish its budget.
The live profile uses disabled thinking, SSE text and no model tools. It is a new
QA prompt with finite validation permission, not automatic promotion of synthetic
G3 semantic capability. UNKNOWN, invalid protocol/identity/usage, source corruption
or expired authority prevents further dispatch. Actual fees remain separately unknown.

G4 does one selected S0/BGE retrieval and one answer request per turn. Follow-ups
may use literal same-chat user messages as explicit search/rerank context in a
`follow_up` V2 query, with a current-turn original-query parent, recorded gap,
source message IDs and preserved original question. No model rewrite, decomposition,
extra retrieval or autonomous research is introduced. Invalidated user premises are
excluded from this projection; claims still require the current source Packet.
Long literal queries fail explicitly, without truncation or a larger BGE budget.

The live system instruction replaces the Mock-only instruction. Whole-message
admission counts the actual serialized envelope and output reserve. Every candidate
is accounted for; full natural Parents are read for admitted evidence after Assembly
for citation viewing. Parent readback does not silently enter the model Packet.
Local ranking outputs seal before reuse; partial local worker outputs block automatic
recomputation into the same root. Sealed calls replay without credentials/network
after expiry. Incompatible checkpoints remain read-only; no historical hashes change.

`GC_CLEAN_CHAT_MODE=LIVE` explicitly selects the opt-in UI; a prepared finite permit
is required. The default is still Mock. Mode-mismatched chats display read-only.
The existing executor launches the real chain in a Job-owned child, and reopening
never schedules paid work. `clean_live` CLI supports prepare/new/submit/continue/show/close;
prepare records explicit operator authority and fixed budget, but sends nothing.
It does not create standing authorization. G5–G7 and default adoption remain separate.

## Stage checks

| Stage | Required evidence / next gate |
| --- | --- |
| G0 | Baseline hash manifest, selected manifests, D01–D24 map, explicit offline boundary. |
| G1 | Actual pinned framework fixture; crash/replay/cancel/budget/lock tests; zero network. |
| G2 | V1-equivalent original query/ranking fixtures; source/Packet tests; saved handoff replay AND finite new-question local S0/BGE run; unchanged protected hashes. |
| G3 | Offline fragmentation/tool/error/recovery fixtures; separately approved finite live probe, sealed raw/usage/tool evidence and scoped capability report. |
| G4 | Full Mock multi-turn/reopen/isolation, context/corrections, idempotent submission, real progress, turn-scoped citations/Parent, cancel/shutdown/recovery and UI fixtures. Real remote dialogue additionally needs matching G3 capability evidence and current batch authorization. |

Tests use small checked-in fixtures. Real local asset checks are explicit opt-in
and use new output roots. Engineering PASS is never final question-answer quality
acceptance, and G2 does not supply an adopted chat application.
