# Release Validation — 2026.09.20.40.6.3

- Python compile: PASS
- Full pytest regression suite: 436/436 PASS
- Vision-overlap dry-run on uploaded processed snapshot: 162 overlaps; 11 applied sweep winners preserved, 151 normal winners
- Endpoint outage regression: retry_count unchanged; one outage file per outage; legacy ConnectError failures requeue
- Stale cleanup: exact preview token required before deletion
- Static asset cache-busting/version alignment: `2026.09.20.40.6.3`
- Deployment action: **Revalidate all + rebuild** before Machine RAG acceptance
