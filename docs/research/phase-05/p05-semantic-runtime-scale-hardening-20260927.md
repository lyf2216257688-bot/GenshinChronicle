# P05 Semantic Runtime Scale Hardening (2026-09-27)

## Scope

This work is provider-free. It changes only the SDK runtime's local evidence
persistence and resume accounting. Prompt v2, the semantic output schema,
source/corpus inputs, Block A, route configuration, and historical evidence
roots are unchanged. No provider request or semantic build was started.

## Changes

- Existing attempt trees are verified once when a route-pair or single-route
  invocation starts. New terminals are verified through the same fail-closed
  artifact checks before they enter the in-memory history ledger. Checkpoint
  aggregation is updated from that ledger, so checkpoint writes no longer
  rescan or rehash historical attempt trees.
- Stream chunks are written as canonical JSONL records in one append-only
  `stream.jsonl` artifact. Each record is flushed and `fsync`ed before the next
  SDK chunk is consumed. The terminal stores only the stream path, SHA-256,
  byte count, chunk count, and format. Historical `stream_chunks` roots remain
  readable.
- `audit_sdk_route_integrity()` is the explicit expensive operation for a
  complete offline artifact hash and accepted-output replay audit. Normal
  resume does not invoke it.
- Missing checkpoints are rebuilt from verified history. A present checkpoint
  with an invalid self-identity, route history, unit state, or attempt count is
  rejected. Issued attempts without terminals, tampered artifacts, ambiguous
  stream descriptors, and secret-bearing evidence remain fail-closed.

The pilot root had 23,274 files for three units, including 23,247 individual
chunk JSON files, and 14,262,658 logical bytes. That is about 7,758 files per
unit before counting directory/MFT overhead; the chunks were the direct cause
of the small-file explosion.

Under the hardened layout, one successful primary attempt uses nine regular
files and about 7.0 KiB logical in the local simulation, or about 36 KiB at a
4 KiB allocation estimate. A fallback attempt has the same per-attempt shape;
the unit total is the sum of its immutable attempts.

## Provider-free verification

The semantic SDK test set passes `31/31`; the related semantic live/compiler
tests pass `29/29`, and the semantic test discovery passes `107/107`.
Historical three-unit pilot evidence remains readable: a full offline audit
passed for 3 units and 3 accepted attempts with zero provider/network calls.

Local route-pair mock simulation used four stream records per unit and measured
the following:

| units | files | logical bytes | 4 KiB file-allocation estimate | initial run | resume | full audit |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 200 | 1,803 | 1,405,928 | 7,385,088 | 7.253 s | 1.116 s | 2.320 s |
| 1,000 | 9,003 | 7,017,666 | 36,876,288 | 33.485 s | 5.596 s | 13.975 s |

The instrumented `_attempts()` call count was exactly one per prepared unit
per invocation (200 on the first run and 200 on resume; likewise 1,000 and
1,000), rather than one historical scan per completed checkpoint. Both resume
runs issued zero provider calls. Every full audit passed with all accepted
attempts replayed offline.

## Extrapolation and boundary

At the measured nine regular files per successful attempt plus three run-level
files, 211,193 units would produce approximately 1,900,740 regular files and
1.48 GB of logical artifact bytes. A 4 KiB allocation estimate is about 7.25
GiB before directory/MFT overhead. A final checkpoint is projected at roughly
11.6 MB. These are linear extrapolations from the fixture simulation, not a
full-corpus run.

For comparison, extrapolating the unchanged pilot layout would have produced
about 1,638,435,294 files, 1.00 TB of logical bytes, and 6.71 TB at a 4 KiB
per-file allocation estimate. The JSONL layout therefore removes the measured
chunk-file multiplier by roughly three orders of magnitude, while preserving
the same per-chunk evidence and hash coverage.

The hard pilot blockers are removed for the next approximately 200-unit
scoped build: stream chunks no longer create one file each, and resume
checkpointing no longer rehashes all historical artifacts after every unit.
Full-corpus execution is still a separate scale problem because even the
compacted per-attempt layout implies roughly 1.9 million files and a large
checkpoint materialization. Any full-corpus decision needs a separately
authorized artifact/index design and measurement; this work does not claim
full-corpus readiness or semantic quality.
