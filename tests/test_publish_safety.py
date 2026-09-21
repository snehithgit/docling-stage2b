from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_gitignore_blocks_secrets_runtime_data_and_databases():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for required in [".env", "/input/", "/converted/", "/processed/", "/data/", "*.db", "__pycache__/", ".pytest_cache/"]:
        assert required in text


def test_publish_scripts_never_delete_git_or_force_push():
    for name in ["upload-github.sh", "upload-github.ps1", "upload-github-stage2c.ps1"]:
        text = (ROOT / name).read_text(encoding="utf-8")
        lowered = text.lower()
        assert "push --force" not in lowered
        assert "push -f" not in lowered
        assert "rm -rf .git" not in lowered
        assert 'remove-item -literalpath ".\\.git"' not in lowered
        assert ".gitignore is missing" in text
        assert "git add -A" in text or "git add -a" in lowered


def test_publish_scripts_refuse_sensitive_staged_files():
    for name in ["upload-github.sh", "upload-github.ps1", "upload-github-stage2c.ps1"]:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "sensitive/runtime files are staged" in text
        assert "sensitive/runtime files are already tracked" in text
        assert "processed" in text
        assert ".env" in text


def test_publish_scripts_allow_safe_env_example_template():
    """The tracked .env.example template is source, not a local secret."""
    sh = (ROOT / "upload-github.sh").read_text(encoding="utf-8")
    assert "grep -vE '(^|/)\\.env\\.example$'" in sh

    for name in ["upload-github.ps1", "upload-github-stage2c.ps1"]:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "-notmatch '(^|/)\\.env\\.example$'" in text
        # The general guard remains in place for real secret variants such as .env.local.
        assert "\\.env($|\\.)" in text


def test_gitignore_explicitly_keeps_env_example():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "!.env.example" in text
