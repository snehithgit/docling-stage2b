# Technical Evidence diagram jobs — V5.1.1

The page now queues diagram work in SQLite. Four dispatcher slots reuse the existing physical worker reservation and health checks. One GPU cannot execute overlapping requests. A browser reload does not lose the queue. Interrupted work resumes after app restart; waiting jobs retry availability without making model requests.

Use Read this diagram with AI for one item. Choose a reading worker and a different review worker, or use automatic allocation. Batch buttons take 25/50/100 existing technical visual entries, deduplicate by picture, skip queued work, completed reviews, and human-confirmed/rejected entries. The next batch advances to remaining items. Re-reading existing accepted decisions is not implicit.

Independent review is a fresh reading of the original image, without showing the previous answer to the reviewer. It compares exact labels, directed/undirected edges and geometry. Both outputs, worker identities and differences are saved. Duplicate labels, unknown directions, unreadable details or geometry differences need inspection. AI agreement is advisory and never writes a human confirmation or makes unvalidated relationships answer-eligible.

Jobs show waiting/running/saved/failed states, actual worker assignment, elapsed time, errors, and comparison buttons. Differences/failed jobs can be filtered. Saved readings are reused unless a selective fresh read is requested. Cancellation affects waiting work; running jobs finish and save normally.

Coverage compares source-item references with searchable information. Its checklist includes pictures, tables and text; a count is not proof that the same number of answers is missing. Logos/icons/controls may need classification. Cards are grouped and paginated; stale checks block text recovery until refreshed. Text recovery adds only eligible complete passages, not diagrams or uncertain tables. Advisory history alone no longer invalidates reference coverage.

APIs: GET/POST /api/postprocess/jobs/{job}/technical-visual-jobs; POST .../cancel. GET/PUT /api/technical-visual-jobs/control supports maintenance pause/drain/resume. Source, image and graph snapshots are rechecked before saving; human decisions made during inference cause the model result to be discarded.
