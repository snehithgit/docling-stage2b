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
