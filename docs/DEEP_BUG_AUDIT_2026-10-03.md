# Docling backend and frontend bug audit — 2026-10-03

## Outcome and scope

Audited main at `c01887f`, following the merged review-loop and anomaly-workspace repairs (PRs #5 and #6). The source fixes in this audit are release `2026.10.03.40.11AH7`. They have not been deployed to `192.168.68.63:8081`.

The repository-wide structural scan covered 39 application Python files, 23 JavaScript files, 22 HTML pages, 150 API routes and 123 literal frontend API calls. Every normalized literal API path matched a backend route. This check does not establish that every request body or response field is correct. Dynamic URLs and DOM references were inspected in the affected workflows; optional and legacy controls were not classified as bugs merely because they were absent from a page.

Deep tracing and regressions focused on conversion/book deletion, Stage 2B dispatch and provider reservations, Stage 2C human authority and generation reconciliation, normal and anomaly review queues, Stage 3 freshness, embedding compatibility, review-worker controls and the chunk viewer. The existing full suite supplies broader coverage for conversion, artifact/visual evidence, structural repairs, retrieval, generation, quotas and worker configuration. This is a source audit with automated regressions, not an exhaustive browser, live Colab or production-load certification.

## Confirmed defects repaired

| ID | Priority | Trigger and previous consequence | Source repair and evidence |
| --- | --- | --- | --- |
| A01 | P1 | Anomaly re-review completed and was queued again within the same clock tick. A timestamp-based unique signature could collide and raise a SQLite error. | UUID request identity; existing anomaly re-review regression now passes. |
| A02 | P1 | Evidence changed A → B → A, or a candidate disappeared then returned. INSERT OR IGNORE found the retired A row but never made it current again, hiding valid review work. | Reactivate the matching evidence row while preserving its status/results. Regression covers reactivation of a failed row and explicit recovery. |
| A03 | P2 | Simultaneous manual queue requests through independent store instances both observed no active job. | BEGIN IMMEDIATE makes deduplication and insertion one serialized database transaction. Parallel-store regression returns the same job ID. |
| A04 | P1 | A capped review failure had no explicit recovery action. The page counted only pending/processing/completed, making unresolved failed work easy to overlook. | Failed count, book links, per-job Retry review button, and a guarded retry API resetting attempts only for current failed rows. Retired, running and completed jobs cannot be reset by this API. |
| A05 | P1 | Another request reserved the Colab provider after scheduler availability checking. Busy responses consumed attempts even though inference never started, eventually failing valid work. | Dedicated busy exception and deferred dispatch without consuming an inference attempt. Regression simulates five successive deferrals. Actual inference errors retain the three-attempt cap. |
| A06 | P1 | Source/proposed evidence changed while a normal AI review was running, but the entry still required review. The old model answer could be published against the new evidence. | Compare evidence signatures again under the ledger lock before publishing. Regression changes evidence during the mocked inference and verifies the answer is discarded without overwriting the ledger. |
| A07 | P1 | Multiple human-reviewed generations competed. A missing visual-decision timestamp returned zero before the text-review timestamp was checked, allowing an older decision to win. | Compare valid decision timestamps, including human_review.saved_at_epoch. Regression verifies text decision timestamps are considered. |
| A08 | P1 | A stale text-review tab submitted an already superseded entry; whitespace-only text passed request-length validation and caused an internal error. | Superseded entry returns 409; invalid correction content returns 422. Both regressions verify no ledger mutation. |
| A09 | P1 | Book deletion raced with human text/vision decisions, verifier bypass or manual anomaly queueing. Late writes could recreate state or enqueue work for the deleted book. | Hold the per-book lifecycle lock from lookup through mutation/queueing. Regression blocks a text write, deletes the authoritative book record, then verifies the write rechecks and returns 404. |
| A10 | P1 | Deleting a book removed the three main pipeline tables but retained its optional review-assistant rows, including manually queued anomalies. | Delete all associated review rows in the same database transaction, retaining compatibility with older databases without that table. Book deletion regression now checks review queue cleanup. |
| A11 | P1 | Quarantine moved a source file, then a later move or manifest allocation failed. Earlier successful moves were not restored; conversion-only deletion also omitted manifest rollback. | Roll back tracked moves on failures anywhere in both quarantine operations. Fault-injection tests fail the second move and verify the original file is restored. |
| A12 | P1 | Storing an advisory normal/anomaly AI audit or anomaly-page choice changed the correction-ledger file hash. Stage 3 became stale despite unchanged accepted content. | Compute ledger freshness from primary evidence and human decisions, excluding advisory AI records/history and update timestamps. Regression verifies advisory writes keep the signature stable while accepted text changes invalidate it. Existing signatures can cause one freshness rebuild after upgrade. |
| A13 | P1 | Legacy machine v1 or complete per-book embedding files existed, but runtime rejected them; a one-time source-patch workflow had never supplied the missing runtime compatibility path. | Validate semantic row identities, model/profile, scope, vector sizes/dimensions and complete coverage before locally migrating/reordering vectors. Changed embedded text remains stale. Existing migration tests pass without an embedding service call. Consume/remove the obsolete one-time patch script and workflow. |
| A14 | P2 | The last text review was saved, but the local entry still looked unreviewed until a page reload. | Update the local human-reviewed state/provenance before rerendering metadata. Python backend tests and JavaScript syntax checks cover the surrounding contracts; this particular presentation change still needs a browser smoke check. |
| A15 | P1 | Chunk searches/detail requests completed in reverse order, or a manual/machine scope changed while an old detail request was pending. The viewer could display the previous selection under the new scope. | Request sequence guards for searches and details; scope changes invalidate old details and clear navigation. Three Node VM behavior tests reverse responses and simulate scope changes. |
| A16 | P2 | Review service stopped and was started again in the same process. Its stop event stayed set; repeated start could also create duplicate supervisors. | Clear the stop event on restart and make start idempotent while a supervisor is running. Source-level inspection and full suite validation; no dedicated restart integration test was added. |
| A17 | P2 | A malformed ledger root or non-object entry reached candidate scanning. | Reject non-object roots and skip non-object candidate entries, avoiding those supervisor crashes. This is narrow defensive handling, not complete corrupted-ledger recovery. |

## Related compatibility corrections

- Manual-type labels and source-file metadata may change without changing the text sent to the embedding model. Reuse matching vectors, refresh machine row metadata, and return the current manual labels in search results. Changed text, headings, row coverage or model/profile still require current embeddings. The outdated manual-label test expectation was corrected accordingly.
- A ranking-only retrieval rule change does not require rebuilding canonical chunks. The contradictory old pipeline-state test now agrees with the existing freshness-reuse contract; no runtime ranking behavior was weakened.
- Source-signature mismatch remains visible as a diagnostic for the request that performed semantic reuse/migration.
- Frontend cache keys and app/nav versions were advanced together across all 22 pages.
- Pull-request CI now runs the new frontend behavior regressions in addition to Python tests and Python/JavaScript syntax checks.

## Validation

Baseline before audit edits: **677 passed, 7 failed, 1 skipped**. Failures included the manual queue signature collision, three missing embedding compatibility paths, one contradictory ranking-drift expectation, and two Windows/Unix permission assertions.

Final local regression run: **693 passed, 1 skipped, 2 deselected**, plus **3 frontend behavior tests passed**. All application Python files compile; every application JavaScript file passes `node --check`; `git diff --check` passes. The two deselected tests assert Unix mode 0600 on secret files, which Windows stat reports as 0666. Their security assertions remain unchanged in source and run in the Linux pull-request CI suite. No production credential or live inference was used for regressions.

Commands:

```text
python -m pytest -q --tb=short -k 'not test_colab_api_key_is_kept_in_mode_0600_secret_file and not test_worker_registry_supports_independent_local_and_multiple_colab_controls'
python -m compileall -q app
node --test tests/frontend/chunks-race.test.cjs
node --check <each app/static/*.js>
git diff --check
```

## Remaining risks and audit limits

1. **Large audit histories:** Some audit/list endpoints cap raw rows at 5,000 before applying later filters (for example text-audit). An older book can therefore be omitted in sufficiently large histories. The current PR does not implement database pagination for all audit endpoints. Treat this as a confirmed scalability gap, not a demonstrated cause of the reported two-book incident.
2. **Supervisor observability:** The review supervisor still catches unexpected exceptions and retries its loop without reporting the exception in its status payload. Malformed root/entry inputs are handled here, but other failures can appear as an idle scheduler. Structured health/error reporting should be a follow-up.
3. **Frontend coverage:** Chunk request races have behavioral regressions. The other pages have source/contract inspection, syntax checks and supporting backend tests, not a full browser interaction suite. Visual layout, reconnect behavior and slow-network races in every other page are not certified.
4. **Deployment and concurrency:** Tests cover SQLite queue concurrency and simulated inference/write races in one application process. Multiple independent application processes do not share the in-memory ledger/lifecycle locks. Deployment should preserve the existing single-server process assumption; this PR does not add distributed locking.
5. **Embedding files:** Compatibility reuse validates semantic identity and expected shapes. This audit does not provide disk-failure atomicity across all three embedding snapshot files or a checksum-based corruption recovery scheme.
6. **Live Colab:** Actual crop quality, model JSON behavior, GPU memory exhaustion and tunnel expiry require a deployed smoke test. The source regressions use deterministic mocked inference. Human decisions remain authoritative; AI re-review is advisory.

## GitHub Linux CI result

[Validation run 37101119796](https://github.com/snehithgit/docling-stage2b/actions/runs/37101119796) passed for source commit `f15c6b3`: **696 Python tests passed**, **3 frontend behavior tests passed**, and all syntax checks passed. This includes both Unix permission assertions excluded locally. [PR #7](https://github.com/snehithgit/docling-stage2b/pull/7) contains the fixes.

## Post-merge verification

After deploying AH7, check that failed reviews appear with a retry action; retry one failed text row and verify attempts restart at one. Queue an anomaly already reviewed by a human and confirm the new AI audit/history is stored without modifying the human decision. Confirm that saving an advisory audit leaves current Stage 3 chunks current, while an accepted human text change makes them stale. Change manual/chunk selections quickly and verify delayed responses cannot replace the active selection. The audit did not modify or restart the running service.
