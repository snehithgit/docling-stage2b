#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

REPO="snehithgit/docling-stage2b"
BRANCH="main"

echo "======================================"
echo " Docling Stage2B GitHub Publisher"
echo "======================================"
echo
echo "Repository: $REPO"
echo "Directory : $(pwd)"
echo

if [ ! -f "Dockerfile" ] || [ ! -d "app" ]; then
    echo "ERROR: Run this script from the docling-stage2b project root."
    exit 1
fi
if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git is not installed. Run: pkg install git -y"
    exit 1
fi
if ! command -v gh >/dev/null 2>&1; then
    echo "ERROR: GitHub CLI is not installed. Run: pkg install gh -y"
    exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
    echo "ERROR: GitHub CLI is not authenticated. Run: gh auth login"
    exit 1
fi

# Remove only obsolete bootstrap artifacts from the earlier failed upload method.
rm -rf buildsrc source
rm -f runtime.tar.xz

# Recreate local Git metadata so the project can safely replace the old repo tree.
rm -rf .git
git init
git branch -M "$BRANCH"
git config user.name "$(gh api user --jq .login)"
git config user.email "$(gh api user --jq '.id')+$(gh api user --jq .login)@users.noreply.github.com"

git add .
echo
echo "Files/changes to publish:"
git status --short

git commit -m "Update Stage 2B OnePlus streaming verification"
git remote add origin "https://github.com/${REPO}.git"

echo
echo "Replacing GitHub main branch with this project tree..."
git push --force origin "$BRANCH"

echo
echo "Source uploaded. GitHub Actions will build:"
echo "ghcr.io/$REPO:latest"
echo
sleep 3
gh run list --repo "$REPO" --workflow docker-publish.yml --limit 3 || true
echo
echo "Watch the newest build with:"
echo "gh run watch --repo $REPO"
