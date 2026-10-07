from tools.audit_v5_release import summarize


def test_release_audit_keeps_content_validation_separate_and_redacts_secrets():
    report = summarize({"version":"5.0.8.2"}, {"books":[{
        "postprocess_job_id":1,"index_ready":True,"identity_integrity":{"ok":True},
        "readiness":{"evidence":{"status":"pending","pending":3,"visual_parse_pending":2}}}]},
        {"review":{"enabled":False},"colab_workers":[{"enabled":True,"api_key":"secret"}]})
    assert report["search_ready_books"] == 1
    assert report["content_status"] == "needs_validation"
    assert "source_validation_pending" in report["content_blockers"][0]["reasons"]
    assert "secret" not in str(report)


def test_empty_inventory_is_not_certified():
    assert summarize({}, {}, {})["content_status"] == "needs_validation"
