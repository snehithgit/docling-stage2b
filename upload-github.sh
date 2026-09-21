#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

REPO="snehithgit/docling-stage2b"
BRANCH="main"
COMMIT_MESSAGE="Update Marine Pipeline Studio source"

fail() { echo "ERROR: $*" >&2; exit 1; }

[ -f Dockerfile ] && [ -d app ] || fail "Run this script from the project root."
[ -f .gitignore ] || fail ".gitignore is missing; refusing to publish."
command -v git >/dev/null 2>&1 || fail "git is not installed."
command -v gh >/dev/null 2>&1 || fail "GitHub CLI is not installed."
gh auth status >/dev/null 2>&1 || fail "GitHub CLI is not authenticated. Run: gh auth login"

PROJECT_DIR="$(pwd -P)"
git config --global --add safe.directory "$PROJECT_DIR" 2>/dev/null || true

# Remove only obsolete build-bootstrap artifacts. Never delete .git.
rm -rf buildsrc source
rm -f runtime.tar.xz

new_repo=0
if [ ! -d .git ]; then
  new_repo=1
  git init
  git remote add origin "https://github.com/${REPO}.git"
  # If the remote already has history, attach this working tree to that history
  # without overwriting local files. This avoids force-push/history destruction.
  if git fetch origin "$BRANCH" >/dev/null 2>&1; then
    git update-ref "refs/heads/$BRANCH" FETCH_HEAD
    git symbolic-ref HEAD "refs/heads/$BRANCH"
    git reset --mixed "refs/heads/$BRANCH" >/dev/null
  else
    git branch -M "$BRANCH"
  fi
else
  current_branch="$(git branch --show-current)"
  [ "$current_branch" = "$BRANCH" ] || fail "Current branch is '$current_branch'. Checkout '$BRANCH' before publishing."
  if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "https://github.com/${REPO}.git"
  else
    git remote add origin "https://github.com/${REPO}.git"
  fi
fi

git config user.name "$(gh api user --jq .login)"
git config user.email "$(gh api user --jq '.id')+$(gh api user --jq .login)@users.noreply.github.com"

git add -A

# Defense in depth: .gitignore is the primary protection, but refuse a publish
# if a sensitive/runtime path is somehow already tracked or force-added.
forbidden_re='(^|/)(\.env($|\.)|data/|input/|converted/|processed/|embedding-cache/|[^/]+\.(db|sqlite|sqlite3)($|-))'
if git ls-files | grep -vE '(^|/)\.env\.example$' | grep -E "$forbidden_re" >/dev/null 2>&1; then
  echo "Refusing to publish because sensitive/runtime files are already tracked:" >&2
  git ls-files | grep -vE '(^|/)\.env\.example$' | grep -E "$forbidden_re" >&2 || true
  exit 2
fi
if git diff --cached --name-only | grep -vE '(^|/)\.env\.example$' | grep -E "$forbidden_re" >/dev/null 2>&1; then
  echo "Refusing to publish because sensitive/runtime files are staged:" >&2
  git diff --cached --name-only | grep -vE '(^|/)\.env\.example$' | grep -E "$forbidden_re" >&2 || true
  exit 2
fi

echo "Files/changes to publish:"
git status --short
if git diff --cached --quiet; then
  echo "No source changes to publish."
  exit 0
fi

git commit -m "$COMMIT_MESSAGE"
# Normal push only. If GitHub is ahead, stop and let the user reconcile history.
git push origin "$BRANCH"

echo "Source uploaded without rewriting Git history."
sleep 3
gh run list --repo "$REPO" --workflow docker-publish.yml --limit 3 || true
