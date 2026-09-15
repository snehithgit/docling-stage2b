from pathlib import Path


def test_all_books_safety_refresh_endpoint_and_ui_contract():
    root = Path(__file__).resolve().parents[1]
    main = (root / "app" / "main.py").read_text(encoding="utf-8")
    html = (root / "app" / "static" / "verification.html").read_text(encoding="utf-8")
    js = (root / "app" / "static" / "verification.js").read_text(encoding="utf-8")
    assert '/api/maintenance/revalidate-all' in main
    assert 'revalidate_saved_pi5_results' in main
    assert 'start_stage2c_backfill' in main
    assert 'stage3_builder.start' in main
    assert 'Revalidate all + rebuild' in html
    assert '/api/maintenance/revalidate-all' in js
