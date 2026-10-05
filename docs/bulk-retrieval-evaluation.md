# Bulk retrieval evaluation

Run against a frozen copy of the app indexes, with the same embedding model and query prefix used to build them. This tool sends query embeddings to the explicitly selected CPU embedding service. It does not generate answers, call Colab, or edit books or ledgers.

The root folder contains `retrieval-status-audit.json` (books), `retrieval-equipment-audit.json` (equipment), and `retrieval-audit-corpus/` (manual retrieval indexes and machine embedding indexes). Questions use the supplied schema: id, split, tier, evaluation_eligible, equipment_id, source_filename, chunk_id, query, evidence_span and page_numbers.

```powershell
python tools/evaluate_bulk_retrieval.py --root PATH --questions QUESTIONS.jsonl --embedding-url http://CPU-EMBEDDINGS:8090 --lexical-only
python tools/evaluate_bulk_retrieval.py --root PATH --questions QUESTIONS.jsonl --embedding-url http://CPU-EMBEDDINGS:8090 --name bulk-full
```

Use a fresh root/cache whenever the corpus, question ordering, model, query prefix or ranking code changes. Query vectors are cached once the embedding phase completes; lexical candidates checkpoint every 100 rows; result JSONL resumes by question ID. Interrupted query embedding currently restarts that phase. Serial initialization upgrades legacy machine metadata before parallel read-only scoring.

Results distinguish exact expected-chunk rank from full expected source-span rank. Gold metrics exclude spans no longer present in the frozen source. Silver questions and changed-source rows are counted separately. Train/dev/test are reported separately; tune using train/dev only. Template questions with ambiguous subjects can have multiple valid sources, so an expected-chunk miss is not necessarily a wrong answer. Passage recall and citation provenance do not establish generated-answer semantic correctness. Evaluate generated answers separately against their cited source pages.

Keep manual excerpts, datasets, cached vectors and reports private; do not commit them to a public repository.
