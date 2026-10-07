"""Read-only release preflight. Content readiness is distinct from software health."""
import argparse
import json
import urllib.request
from pathlib import Path


def summarize(version, retrieval, workers):
    books = retrieval.get("books") or []
    blockers = []
    for book in books:
        evidence = (book.get("readiness") or {}).get("evidence") or {}
        reasons = []
        if not book.get("index_ready"):
            reasons.append("search_index_not_ready")
        if not (book.get("identity_integrity") or {}).get("ok"):
            reasons.append("source_identity_unconfirmed")
        if evidence.get("migration_required"):
            reasons.append("evidence_migration_required")
        if evidence.get("status") in {"invalid", "stale", "legacy", "not_scanned"}:
            reasons.append("evidence_" + evidence["status"])
        if evidence.get("pending"):
            reasons.append("source_validation_pending")
        if not evidence.get("whole_manual_coverage_measured"):
            reasons.append("whole_manual_coverage_unmeasured")
        reference = evidence.get("source_reference_coverage") or {}
        if reference.get("status") != "current":
            reasons.append("source_reference_coverage_not_current")
        if reference.get("pending_source_review"):
            reasons.append("source_reference_gaps_pending")
        if reasons:
            blockers.append({"job_id": book.get("postprocess_job_id"), "reasons": reasons,
                             "pending_candidates": evidence.get("pending"),
                             "visual_parse_pending": evidence.get("visual_parse_pending")})
    return {"version": version.get("version"), "books": len(books),
            "search_ready_books": sum(bool(b.get("index_ready")) for b in books),
            "content_status": "needs_validation" if blockers or not books else "tracked_coverage_resolved",
            "content_blockers": blockers,
            "review_dispatch_enabled": bool((workers.get("review") or {}).get("enabled")),
            "enabled_colab_workers": sum(bool(w.get("enabled")) for w in workers.get("colab_workers") or []),
            "credentials_included": False, "model_calls": 0, "writes_to_deployment": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    def get(path):
        with urllib.request.urlopen(args.base_url.rstrip("/") + path, timeout=60) as response:
            return json.load(response)
    report = summarize(get("/api/version"), get("/api/retrieval/status"), get("/api/workers"))
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ["version", "books", "search_ready_books", "content_status"]}))

if __name__ == "__main__":
    main()
