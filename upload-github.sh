#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

GITEA_REPO_URL="${GITEA_REPO_URL:-ssh://git@192.168.68.63:222/snehith/docling-stage2b.git}"
GITEA_SSH_KEY="${GITEA_SSH_KEY:-$HOME/.ssh/gitea_mobile}"
GITHUB_REPO_URL="https://github.com/snehithgit/docling-stage2b.git"
BRANCH="main"
COMMIT_MESSAGE="Update Marine Pipeline Studio source"

fail() { echo "ERROR: $*" >&2; exit 1; }

[ -f Dockerfile ] && [ -d app ] || fail "Run this script from the project root."
[ -f .gitignore ] || fail ".gitignore is missing; refusing to publish."
command -v git >/dev/null 2>&1 || fail "git is not installed."

case "$GITEA_REPO_URL" in
  git@*|ssh://*)
    [ -f "$GITEA_SSH_KEY" ] || fail "Gitea SSH private key not found: $GITEA_SSH_KEY"
    export GIT_SSH_COMMAND="ssh -i $GITEA_SSH_KEY -o IdentitiesOnly=yes"
    ;;
esac

PROJECT_DIR="$(pwd -P)"
git config --global --add safe.directory "$PROJECT_DIR" 2>/dev/null || true

# Remove only obsolete build-bootstrap artifacts. Never delete .git.
rm -rf buildsrc source
rm -f runtime.tar.xz

new_repo=0
if [ ! -d .git ]; then
  new_repo=1
  git init
  git branch -M "$BRANCH"
else
  current_branch="$(git branch --show-current)"
  [ "$current_branch" = "$BRANCH" ] || fail "Current branch is '$current_branch'. Checkout '$BRANCH' before publishing."
fi

if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$GITEA_REPO_URL"
else
  git remote add origin "$GITEA_REPO_URL"
fi
if git remote get-url github >/dev/null 2>&1; then
  git remote set-url github "$GITHUB_REPO_URL"
else
  git remote add github "$GITHUB_REPO_URL"
fi

[ -n "$(git config user.name || true)" ] || git config user.name "snehithgit"
[ -n "$(git config user.email || true)" ] || git config user.email "64060670+snehithgit@users.noreply.github.com"

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
  echo "No new local source changes to commit."
else
  git commit -m "$COMMIT_MESSAGE"
fi

# Incorporate changes pushed from the other device before publishing.
if ! remote_heads="$(git ls-remote --heads origin "refs/heads/$BRANCH")"; then
  fail "Cannot contact the Gitea repository at $GITEA_REPO_URL"
fi
needs_push=1
if [ -n "$remote_heads" ]; then
  git fetch origin "$BRANCH"
  git rebase "origin/$BRANCH" || fail "Resolve the conflicts, run 'git rebase --continue', then run this script again."
  if [ "$(git rev-parse HEAD)" = "$(git rev-parse "origin/$BRANCH")" ]; then
    needs_push=0
  fi
fi

# Gitea mirrors this normal push to GitHub, where Actions runs.
if [ "$needs_push" -eq 1 ]; then
  git push origin "$BRANCH"
  echo "Source synced to Gitea without rewriting Git history."
  echo "Gitea will mirror this commit to GitHub, which will start GitHub Actions."
else
  echo "Local and Gitea main are already synchronized; no push was needed."
fi
