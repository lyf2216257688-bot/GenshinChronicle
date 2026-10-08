# Historical-200 Item Mechanical Repair Routing

## Scope and Evidence

This user-authorized provider-free investigation consumes the frozen Source
Anchor v0.1 sibling replay at
`.local/p05-w2-historical-atomic-replay-audit-20261007-r2/source-anchor-v01-replay.json`.
The final research artifact is
`data/retrieval/p05-w2-mechanical-repair-routing-20261007-r3/report.json`.
Earlier routing r1/r2 artifacts are intermediate implementation checkpoints,
not final authority. The analyzer is
`src/genshin_corpus/retrieval/historical_atomic_repair_routing.py`.

The artifact reads all three manifests, sample payloads, actual canonical
outputs, terminal, validation and request-metadata evidence. It verifies
manifest identities, payload/output/item identities, runtime dispositions,
replay item accounting and classification-ledger hash. It checks every read
artifact hash again before writing. No historical artifact was overwritten.
The input hash inventory is retained in the report. The report is not a new
strict validator verdict and is not semantic approval.

## Route Definitions

- `CONTRACT_FIXABLE`: one literal occurrence in one eligible bound source
  content value establishes a deterministic binding candidate. A future
  research contract could identify it without rewriting the quote, changing
  source refs or generating semantics. This investigation does not approve it.
- `MODEL_REPAIRABLE`: output-level correction/regeneration candidate, not proof
  that a model can repair it. Existing fabricated/unbound and serialized
  metadata rejections cannot become contract-admitted by a new literal search.
  Correction may instead result in explicit omission; neither is executed.
- `UNKNOWN`: competing source values/occurrences or unavailable source role.
  Do not automatically send to a model or admit through a contract.
- `NON_SEMANTIC_METADATA`: actual source evidence establishes a resource/UI
  locator or component assertion, not natural semantic text. Retain in an
  inactive ledger; do not silently drop or turn it into PASS.

Every item has `semantic_correctness=UNKNOWN`. Model necessity, model success,
semantic truth, recall and production usability are not established. The old
metadata failure class describes quote contamination, not an automatic finding
that the associated content is non-semantic.

## Exact Item Distribution

| Prior failed-item class | CONTRACT_FIXABLE | MODEL_REPAIRABLE | UNKNOWN | NON_SEMANTIC_METADATA | Total |
|---|---:|---:|---:|---:|---:|
| Projection/binding | 168 | 1 | 81 | 1 | 251 |
| Structured boundary remaining | 34 | 0 | 10 | 40 | 84 |
| Fabricated/unbound | 0 | 262 | 0 | 41 | 303 |
| Serialized metadata contamination | 0 | 238 | 0 | 7 | 245 |
| Total remaining failed | 202 | 501 | 91 | 89 | 883 |

The full original 945 failed-item ledger is retained: 883 remaining failed plus
62 already approved Source Anchor v0.1 items. Those 62 are recorded separately
as `already_anchor_approved`, not counted as newly routed/recovered. They retain
their prior sibling verdict and have route CONTRACT_FIXABLE, so all 945 original
failed items have one of the four route values (264/501/91/89 overall).

The 335 focused projection/structured items contain 202 deterministic binding
candidates, 1 output binding intervention candidate, 91 UNKNOWN and 41
non-semantic metadata items. Of the 202 candidates, 181 are bound literal-span
candidates and 21 are parsed HTML name/amount attribute-pair candidates.
Neither operation is implemented as a validator or automatic repair here.

168 projection candidates occur across 32 units; 34 structured candidates
across 12 units. These overlap, giving 40 units containing a contract candidate.
The only projection MODEL_REPAIRABLE item occurs in one unit. The 81 projection
UNKNOWN items occur in 24 units; the 10 structured UNKNOWN items in one unit.
The 40 structured metadata items occur in one unit.

UNKNOWN decomposes into 81 ambiguous bound values/occurrences and 10 items with
no eligible content location (for example section/tab headings). Deterministic
repair of these is not demonstrated. The 501 model routes comprise 500
quote/binding intervention candidates from the old fabricated/contamination
classes and one quote found only in unbound natural text. This is not evidence
that 501 paid requests are required, nor permission to dispatch any request.

## Unit Aggregation

Unit routing preserves all item routes/counts and uses blocking precedence
UNKNOWN, MODEL_REPAIRABLE, NON_SEMANTIC_METADATA, CONTRACT_FIXABLE. This is a
queue-routing summary, not validator PASS. A metadata item is not an accepted
semantic item even if all other items are contract candidates.

| Exclusive routing summary | Canonical-fail units |
|---|---:|
| CONTRACT_FIXABLE | 6 |
| MODEL_REPAIRABLE | 51 |
| UNKNOWN | 25 |
| NON_SEMANTIC_METADATA | 1 |
| Total | 83 |

Overlapping route-presence counts are contract 40, model 66, UNKNOWN 25,
metadata 13; these must not be summed as exclusive units.

| Exact unit route set | Units |
|---|---:|
| UNKNOWN | 4 |
| CONTRACT_FIXABLE + UNKNOWN | 6 |
| CONTRACT_FIXABLE + MODEL_REPAIRABLE + UNKNOWN | 9 |
| MODEL_REPAIRABLE | 22 |
| MODEL_REPAIRABLE + UNKNOWN | 5 |
| CONTRACT_FIXABLE + MODEL_REPAIRABLE | 18 |
| MODEL_REPAIRABLE + NON_SEMANTIC_METADATA | 11 |
| CONTRACT_FIXABLE | 6 |
| NON_SEMANTIC_METADATA | 1 |
| All four routes | 1 |

The six pure contract-candidate units are:

| Unit ID | Title | Remaining candidate items |
|---|---|---:|
| `207e61c1ee31a9a08c07ae206cb09cf5629bc3c1cbc9e1f6bab9bb56eeeb4d1d` | 空月之歌 第六幕「散于晨雾的月芒」 | 6 |
| `63636df90f16a60494ff9f976b5e11131c592cc4dc7fd0fc647c82e023402fca` | 海妮耶 | 2 |
| `65d26338764b46bfcbf201fe27073b064e769ebe944ca3dc67a660f5ca610ddd` | 神樱大祓 | 1 |
| `9429ff71c478843e52bdce6f317b6cba6f7287b5a69425f069091351a5fe6202` | 香木「赤璋」 | 6 |
| `d9e8790e4b92297cc1dda73ce3672f254b089ba490c409a49107b38fe001d14b` | 雪山迷踪 | 1 |
| `f255a6f8fe19d05dc70f252c33c2672cc211b198d423bc22c0c5837e5073478e` | 被藏匿的账本 | 3 |

All six remain strict/v0.1 FAIL. This investigation recovers zero items or units.
Original accounting remains 200 logical / 107 strict PASS / 83 canonical FAIL /
1 no canonical / 9 omitted. The no-canonical unit adds one UNKNOWN outside
the 83 (26 total unit UNKNOWN). The 107 PASS units have no remaining failures;
Source Anchor v0.1 stays a research sibling, not production adoption.

## Representative Artifact Cases

- `09627a.../f1`, 兰贡迪的花: quote `兰贡迪赠送的花儿` exists once in the
  bound segment's `/value/decoded/attr/0/value/0`; the separate text projection
  also exists outside refs. This is a bound literal-span contract candidate,
  not permission to silently change refs. Its `m2` quote `兰贡迪` appears in
  multiple bound values and remains UNKNOWN.
- `1a57ce.../a11`, 水煮黑背鲈: actual HTML tag has `data-entry-name="鱼肉"`
  and `data-entry-amount="3"`; the exact pair occurs once in bound
  `/value/decoded/materials/value`. A structured attribute-pair anchor is a
  deterministic candidate; model label correctness remains UNKNOWN.
- `207e61.../i114`: the quoted question/answer HTML exists literally in a bound
  dialogue node value. A future literal-span contract can preserve its hash
  and codepoint offsets without flattening or changing the quote. Attribution
  and dialogue interpretation are not reviewed.
- `f696c5.../i7`, 干练短袖帽衫·阳橙-男: quote `明丽珍材·精良*3` exists
  only outside declared supporting refs; routing is MODEL_REPAIRABLE, not a
  deterministic rebinding approval.
- `d810ca.../f_mat1_id,img,link,ratio`, 水畔的宁静时光: actual HTML attributes
  establish resource ID/image/link/display ratio. These are NON_SEMANTIC_METADATA;
  name/amount candidates in the same unit are retained separately.
- `069584.../m1`, 仁羽: quote is in `section` headers, not an eligible source
  content field. UNKNOWN, not an automatically admitted natural quote.
- `119cbe.../f1`, 沉玉谷: old metadata contamination quote `支援牌` is not by
  itself proof of a non-semantic item. It remains an output-intervention
  candidate, never contract-admitted by this investigation.

## Minimum Repair Queue Proposal

Queue entries identify, rather than copy/replace, an immutable original item:

```json
{
  "queue_id": "sha256(policy + replay + unit_id + local_id)",
  "routing_policy_identity": "version + implementation hash",
  "unit_id": "compilation identity",
  "record_key": "source record key",
  "local_id": "original item local ID",
  "original_item_identity": "sha256",
  "payload_identity": "sha256",
  "canonical_output_identity": "sha256",
  "canonical_path": "immutable canonical output path",
  "replay_identity": "sha256",
  "historical_runtime_verdict": "preserved",
  "strict_replay_verdict": "preserved",
  "source_anchor_item_verdict": "preserved",
  "failure_class": "preserved",
  "route": "one of the four routes",
  "reason": "artifact-based reason",
  "source_segment_ids": ["original refs"],
  "source_quote": "original quote",
  "evidence_locations": ["segment ID + pointer + value hash + offsets/attributes"],
  "dependency_local_ids": ["subject/object/participants and whole-output context"],
  "operation_candidate": "proposed operation, not executed",
  "output_disposition": "new sibling only; preserve original and omissions",
  "semantic_correctness": "UNKNOWN",
  "execution_authorized": false
}
```

UNKNOWN is blocked, metadata is quarantined with accounting, and contract
candidates need a separately versioned research admission test. Model-route
entries need separate execution authorization and a pinned output contract;
repairability is unproven. No queue consumer or automatic repair is implemented.

## Verification and One Next Work Unit

Focused routing/audit/two-stage tests passed 29/29 (11 new routing tests).
The final full historical artifact investigation ran with socket connect
blocked, provider/network calls = 0, and input hashes unchanged. No Prompt,
production validator, model output, historical root or existing current-phase
modification was changed. No semantic reviewer, risk score, Navi/KG, Full Build
or push was performed.

Recommend only one next work unit: implement a research-only, provider-free
bound literal-span anchor sibling for the six pure CONTRACT_FIXABLE units
(19 remaining items), using the immutable outputs and pointer/value-hash/
codepoint-span checks. Keep metadata and fabricated classes fail closed,
exercise ambiguity/tamper negatives and all 107 strict PASS controls, retain
all original item/unit verdicts, and do not include the HTML name/amount
attribute-pair extension or model repair in that unit.

This recommendation describes the routing checkpoint. The subsequently
authorized six-unit/19-item literal-span implementation and replay are owned by
`p05-w2-bound-literal-span-sibling-20261008.md`; routing r3 remains immutable
investigation evidence, not its resulting admission verdict.
