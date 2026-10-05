# Automatic detected-anomaly review (AH17)

When reviews are enabled and an anomaly worker is assigned, detected text, table-cell, visual, and structural anomalies without human decisions enter the durable queue automatically. Jobs remain visible while primary verification runs; dispatch still waits for primary Text/Vision/Artifact work to finish. Saved audit results do not schedule themselves again for unchanged evidence. Failed jobs retain the existing bounded retry policy.

Human-verified or human-classified findings are excluded from automatic review. The Yes/No batch action now applies only to detected anomalies with existing human decisions. Yes requests a fresh advisory audit, and No leaves human decisions unchanged while ordinary automatic anomaly review continues. Individual explicit review actions remain available. None of these jobs applies a correction or overwrites a human decision automatically.

Regression coverage includes automatic text/table-cell/vision/structural queuing, excluded human decisions and declined findings, unchanged completed-job deduplication, queuing during primary-work barriers, and human-only batch re-review.

AH18 also recovers completed legacy jobs whose ledger audit is missing, stale, discarded, or lacks text source validation. Existing attempt counts are retained and invalid completions stop after three attempts. Current validated audits and human decisions are never automatically re-reviewed.
