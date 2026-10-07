# V5.0.9.3 duplicate work audit

Automatic reviews remain selective: only suspicious text and visual evidence
requiring attention enter normal review. Provider names do not imply accuracy.
Human-approved entries require an explicit re-review request.

Fixed in this release:

- Normal and automatic anomaly review of the same item no longer run together.
- Publishing a normal recommendation retires older pending automatic anomaly
  audits atomically. Discovery schedules only anomalies still present afterward.
- Different items still run in parallel. Explicit human re-review stays intact.
- Disabling normal review assignments does not strand anomaly-only work.
- Review status polling no longer reloads assignment controls every five seconds.

Existing protections retained:

- Stable review signatures reuse completed reviews for unchanged evidence.
- Endpoint failures use exponential cooldown; malformed reviews have bounded retries.
- Equipment embedding builds reuse unchanged vectors under compatible model settings.
- Validated technical extraction can reuse its current evidence signature.
- Artifact sweep excludes pictures already handled by the ordinary visual route.
- Books, Processing, Review, Ask and Settings are the primary navigation; specialist
  editors remain available through those workflows and Advanced.

Limits: corrections can legitimately invalidate downstream reading order and chunk
boundaries, so this release does not assume that only one chunk must be rebuilt.
It does not share model responses across different tasks, prompts or model versions.
Those are distinct evidence operations. Source validation, human authority and
stale-result guards are preserved.
