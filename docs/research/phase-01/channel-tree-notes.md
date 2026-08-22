# Channel Tree Notes

Status: 01A complete; facts below are observations from the reviewed current capture.

This file records the human-readable analysis of the current channel tree. Do not paste secrets or unredacted cURL here.

## Capture metadata

```text
Observed at:
Request method:
Request URL:
HTTP status:
Raw response local path:
Raw response hash (optional):
```

## Tree summary

```text
Top-level channel count: 10 (observed in this capture)
Total channel/node count: 96 (observed in this capture)
Maximum depth:
Leaf count:
Non-leaf count:
Unknown node shapes:
```

## Channel table

Fill from observed JSON rather than guessing.

| channel_id | parent_id | name | depth | leaf? | enumerable? | structural notes |
|---:|---:|---|---:|---|---|---|
| | | | | | | |

## Representative channels for 01B

Selection should maximize structural/content diversity, not simply pick the first channels.

| channel | channel_id | why selected | listing sample status |
|---|---:|---|---|
| representative channel | 43 | cross-channel listing sample | reviewed |
| representative channel | 25 | cross-channel listing sample | reviewed |
| representative channel | 233 | cross-channel listing sample | reviewed |
| representative channel | 129 | cross-channel listing sample | reviewed |
| representative channel | 267 | cross-channel listing sample | reviewed |
| representative channel | 81 | cross-channel listing sample | reviewed |

## Observed structural constraints

- The reviewed capture includes `shortcut`, `hidden`, and special-function nodes.
- The tree is not limited to `depth=2`.
- A `list` embedded in the map response is not a complete content inventory.

## Unknowns / anomalies

- Permanent counts and API-wide invariants are not inferred from this capture.

## Decision gate

01A is complete for the reviewed capture; representative 01B channels remain recorded here, while their API calls, schemas, and samples are maintained in `mihoyo-obc-api-discovery.md`.
