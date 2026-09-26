# P05-W2 DeepSeek stream interruption investigation (2026-09-26)

## Finding

The r2 interruption was not a timeout, a pre-generation policy response, or a
clean output-budget stop. The saved tail contains ordinary reasoning deltas
with no `finish_reason`; the pinned OpenAI SDK 3.19.2 then raised
`APIError("Upstream response stream ended before completion")` while the HTTP
status remained 200. A zero-network MockTransport reproduction with a normal
SSE chunk followed by an SSE JSON `error` event produced the same exception
path. The immediate mechanism is therefore an in-band provider/upstream SSE
error event after generation had started.

The r2 runner saved only the exception message. It did not retain the SDK
exception body's structured error code/type or the raw SSE error frame, so the
responsible layer and exact provider error code remain `UNKNOWN`. The stream
ID was `3e7887c2ca9a525ce66fd15875f33b9f`; the TokenMetro request window was
2026-09-26 15:09:31 to 15:09:41 local (+08:00). No persisted `x-request-id`
was available. These identifiers are sufficient for a provider-side log
lookup without disclosing credentials.

## Reproduction and recovery

The exact r2 logical request was issued once more in a fresh root,
`data/retrieval/p05-w2-deepseek-route-stream-error-diagnostic-20260926-r3`,
with the same Prompt v2, payload, model, request identity, and wire bytes.
This call completed on TokenMetro: HTTP 200, 2,537 chunks,
`finish_reason=stop`, 282 visible characters, local contract PASS, and Jizhi
not called. The provider request ID was
`1fa9965016123d6232a2cdd90475d4b2`. The r2 and r3 wire SHA-256 is identical:
`7a9a93f807addf24e87e9fab891baa8ec2b2339f0f39aa556d583160f359fc64`.
R3's raw response envelope SHA-256 is
`0c62bc8cf5558cb2f9188c906d331885c8d68bcb9aac151a3479ff2cd9f4016d`.

This exact-request recovery shows that the interruption is intermittent at
the tested route/time. It does not prove that the route is stable, identify
the upstream owner, or establish real fallback behavior.

## Remediation

The route-pair runner now preserves bounded, credential-redacted
`provider_error_code`, `provider_error_type`, and `provider_error_param`
fields when the pinned SDK exposes them for an HTTP 200 `APIError`. A focused
MockTransport regression covers the SSE error event, redaction, immutable
attempt evidence, and the required no-fallback decision; the SDK test module
passes 28/28. This repair improves future diagnosis without changing the
production safety boundary.

An incomplete or partial stream remains ineligible for Jizhi fallback because
the provider may already have generated or billed content. Recovery requires a
new root and explicit authorization after the failed attempt is accounted for;
the original r2 root cannot be resumed or reissued. No automatic retry was
added, and no further live request follows r3.

Both r2 and r3 roots passed offline artifact hash/byte-count and secret-safety
checks. The route-pair result remains **real fallback acceptance: NOT PASS**;
neither request received the exact pre-generation policy 403 that permits a
Jizhi transition.
