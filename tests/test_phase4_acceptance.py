from app import main
from app.pipeline_state import required_verification_state, resolve_pipeline_stage


def _verification(*, ready: bool, failed: int = 0, pending: int = 0, processing: int = 0):
    return {
        "ready": ready,
        "failed": failed,
        "pending": pending,
        "processing": processing,
        "completed": 1 if ready else 0,
        "raw_total": 1 if (ready or failed or pending or processing) else 0,
        "discovery_current": True,
    }


def test_canonical_pipeline_acceptance_matrix():
    cases = [
        (
            "analysis",
            dict(
                stage2a_ready=False,
                verification=_verification(ready=False),
                stage2c_ready=False,
                structural_review_pending=0,
                verifier_audit_pending=0,
                verifier_audit_blocking=0,
                stage3_ready=False,
            ),
            "stage2a",
        ),
        (
            "verification_pending",
            dict(
                stage2a_ready=True,
                verification=_verification(ready=False, pending=1),
                stage2c_ready=False,
                structural_review_pending=0,
                verifier_audit_pending=0,
                verifier_audit_blocking=0,
                stage3_ready=False,
            ),
            "stage2b",
        ),
        (
            "verification_failed",
            dict(
                stage2a_ready=True,
                verification=_verification(ready=False, failed=1),
                stage2c_ready=False,
                structural_review_pending=0,
                verifier_audit_pending=0,
                verifier_audit_blocking=0,
                stage3_ready=False,
            ),
            "stage2b",
        ),
        (
            "stage2c_stale",
            dict(
                stage2a_ready=True,
                verification=_verification(ready=True),
                stage2c_ready=False,
                stage2c_reason="stage2c_stale_after_verification",
                structural_review_pending=0,
                verifier_audit_pending=0,
                verifier_audit_blocking=0,
                stage3_ready=False,
            ),
            "stage2c",
        ),
        (
            "structural_review",
            dict(
                stage2a_ready=True,
                verification=_verification(ready=True),
                stage2c_ready=True,
                structural_review_pending=2,
                verifier_audit_pending=0,
                verifier_audit_blocking=0,
                stage3_ready=False,
            ),
            "stage2a_human_review",
        ),
        (
            "verifier_audit",
            dict(
                stage2a_ready=True,
                verification=_verification(ready=True),
                stage2c_ready=True,
                structural_review_pending=0,
                verifier_audit_pending=3,
                verifier_audit_blocking=2,
                stage3_ready=False,
            ),
            "verifier_audit",
        ),
        (
            "stage3_stale",
            dict(
                stage2a_ready=True,
                verification=_verification(ready=True),
                stage2c_ready=True,
                structural_review_pending=0,
                verifier_audit_pending=0,
                verifier_audit_blocking=0,
                stage3_ready=False,
                stage3_reason="stage3_stale_after_stage2c",
            ),
            "stage3",
        ),
        (
            "testing_bypass",
            dict(
                stage2a_ready=True,
                verification=_verification(ready=True),
                stage2c_ready=True,
                structural_review_pending=0,
                verifier_audit_pending=3,
                verifier_audit_blocking=0,
                audit_bypassed=True,
                stage3_ready=False,
            ),
            "stage3",
        ),
        (
            "post_stage3",
            dict(
                stage2a_ready=True,
                verification=_verification(ready=True),
                stage2c_ready=True,
                structural_review_pending=0,
                verifier_audit_pending=0,
                verifier_audit_blocking=0,
                stage3_ready=True,
            ),
            "post_stage3",
        ),
    ]
    for name, kwargs, expected in cases:
        state = resolve_pipeline_stage(**kwargs)
        assert state["next_stage"] == expected, name


def test_clean_zero_route_book_advances_beyond_verification():
    verification = required_verification_state(
        [],
        discovery_current=True,
        artifact_sweep_required=True,
        expected_total=0,
    )
    assert verification["ready"] is True
    state = resolve_pipeline_stage(
        stage2a_ready=True,
        verification=verification,
        stage2c_ready=False,
        stage2c_reason="stage2c_not_built",
        stage3_ready=False,
    )
    assert state["next_stage"] == "stage2c"


def test_machine_projection_acceptance_matrix():
    base = {"pipeline": {"next_stage": "post_stage3", "stage3_ready": True}}
    unassigned = {"pipeline": dict(base["pipeline"])}
    main._apply_document_machine_state(unassigned, None, None)
    assert unassigned["pipeline"]["next_stage"] == "assign_machine"

    stale = {"pipeline": dict(base["pipeline"])}
    owner = {"equipment_id": "eq-pump", "name": "Bilge Pump"}
    main._apply_document_machine_state(
        stale,
        owner,
        {"ready": False, "reason": "hybrid_stale", "rows": 110},
    )
    assert stale["pipeline"]["next_stage"] == "machine_embedding"
    assert stale["pipeline"]["machine_embedding_ready"] is False

    ready = {"pipeline": dict(base["pipeline"])}
    main._apply_document_machine_state(
        ready,
        owner,
        {"ready": True, "reason": None, "rows": 110},
    )
    assert ready["pipeline"]["next_stage"] == "rag_ready"
    assert ready["pipeline"]["machine_embedding_ready"] is True
    assert ready["pipeline"]["machine_embedding_rows"] == 110


def test_machine_projection_never_overrides_review_blocker():
    row = {
        "pipeline": {
            "next_stage": "stage2a_human_review",
            "stage3_ready": True,
            "stage2a_human_review_pending": 1,
        }
    }
    main._apply_document_machine_state(
        row,
        {"equipment_id": "eq-crane", "name": "Crane"},
        {"ready": True, "rows": 400},
    )
    assert row["pipeline"]["next_stage"] == "stage2a_human_review"
