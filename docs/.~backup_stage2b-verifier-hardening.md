# Stage 2B verifier hardening

## Pi5 evidence gate

Pi5 is still a triage verifier, not a correction engine. The model receives nearby
same-page context, but a decisive result is accepted only when its `evidence`
field can be found in `SUSPECT TEXT` after representation-only normalization
(Unicode compatibility, case, whitespace, quotation marks, and spacing around
punctuation/units). There is no semantic or fuzzy matching.

If evidence is missing or comes only from nearby context, the stored Stage 2B
result is completed as `UNCERTAIN` with `reason_code=INVALID_EVIDENCE`. The raw
model verdict/reason are preserved as `model_verdict` and `model_reason_code`.
Historical artifacts are never rewritten; rerun a route to apply the new gate.

## OnePlus parse recovery

A malformed vision response gets one compact JSON-only repair request. If both
responses are still unparseable, the route is completed conservatively as
`UNCERTAIN` with `unresolved_reason=MODEL_RESPONSE_PARSE_FAILED`. Both raw
attempts remain in the result audit. This condition is no longer a permanent
`VisionParseError` failure.

When the full-image response cannot be parsed, crop fan-out is skipped for that
attempt. This prevents a formatting problem from consuming the entire OnePlus
route budget. If the full image parses successfully but an individual crop does
not, that crop is recorded as `parse_uncertain`, other crops continue, and the
merge remains conservative.

Transport failures, HTTP 408/429/5xx, and timeouts retain the existing retry and
backoff behavior; parser recovery does not hide network/server failures.
