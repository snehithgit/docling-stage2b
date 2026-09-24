param(
    [string]$GiteaRepoUrl = $(if ($env:GITEA_REPO_URL) { $env:GITEA_REPO_URL } else { "http://192.168.68.63:3002/snehith/docling-stage2b.git" })
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$GitHubRepoUrl = "https://github.com/snehithgit/docling-stage2b.git"
$Branch = "main"
$CommitMessage = "Update Marine Pipeline Studio source"

function Assert-LastExitCode {
    param([string]$Message)
    if ($LASTEXITCODE -ne 0) { throw "$Message (exit code $LASTEXITCODE)" }
}

if (-not (Test-Path -LiteralPath ".\Dockerfile" -PathType Leaf) -or
    -not (Test-Path -LiteralPath ".\app" -PathType Container)) {
    throw "Run this script from the project root."
}
if (-not (Test-Path -LiteralPath ".\.gitignore" -PathType Leaf)) {
    throw ".gitignore is missing; refusing to publish."
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git is not installed or not in PATH." }

# Remove only obsolete build-bootstrap artifacts. Never delete .git.
foreach ($Path in @(".\buildsrc", ".\source")) {
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Recurse -Force }
}
if (Test-Path -LiteralPath ".\runtime.tar.xz") { Remove-Item -LiteralPath ".\runtime.tar.xz" -Force }

if (-not (Test-Path -LiteralPath ".\.git" -PathType Container)) {
    & git init
    Assert-LastExitCode "git init failed"
    & git branch -M $Branch
    Assert-LastExitCode "Failed to create branch $Branch"
} else {
    $CurrentBranch = (& git branch --show-current).Trim()
    Assert-LastExitCode "Failed to read current Git branch"
    if ($CurrentBranch -ne $Branch) { throw "Current branch is '$CurrentBranch'. Checkout '$Branch' before publishing." }
}

# Gitea is the shared source of truth. Gitea's push mirror sends main to GitHub,
# where docker-publish.yml runs on GitHub-hosted Actions.
$Remotes = @(& git remote)
Assert-LastExitCode "Failed to list Git remotes"
if ($Remotes -contains "origin") {
    & git remote set-url origin $GiteaRepoUrl
    Assert-LastExitCode "Failed to update the Gitea origin remote"
} else {
    & git remote add origin $GiteaRepoUrl
    Assert-LastExitCode "Failed to add the Gitea origin remote"
}
if ($Remotes -contains "github") {
    & git remote set-url github $GitHubRepoUrl
    Assert-LastExitCode "Failed to update the GitHub reference remote"
} else {
    & git remote add github $GitHubRepoUrl
    Assert-LastExitCode "Failed to add the GitHub reference remote"
}

& git config user.name "snehithgit"
Assert-LastExitCode "Failed to configure the Git author name"
& git config user.email "64060670+snehithgit@users.noreply.github.com"
Assert-LastExitCode "Failed to configure the Git author email"

& git add -A
Assert-LastExitCode "git add failed"

$Forbidden = '(^|/)(\.env($|\.)|data/|input/|converted/|processed/|embedding-cache/|[^/]+\.(db|sqlite|sqlite3)($|-))'
$Tracked = @(& git ls-files)
Assert-LastExitCode "Failed to inspect tracked files"
$TrackedUnsafe = @($Tracked | Where-Object { $_ -notmatch '(^|/)\.env\.example$' -and $_ -match $Forbidden })
if ($TrackedUnsafe.Count -gt 0) {
    Write-Error ("Refusing to publish because sensitive/runtime files are already tracked:`n" + ($TrackedUnsafe -join "`n"))
}
$Staged = @(& git diff --cached --name-only)
Assert-LastExitCode "Failed to inspect staged files"
$Unsafe = @($Staged | Where-Object { $_ -notmatch '(^|/)\.env\.example$' -and $_ -match $Forbidden })
if ($Unsafe.Count -gt 0) {
    Write-Error ("Refusing to publish because sensitive/runtime files are staged:`n" + ($Unsafe -join "`n"))
}

Write-Host "Files/changes to publish:"
& git status --short
Assert-LastExitCode "git status failed"
& git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    & git commit -m $CommitMessage
    Assert-LastExitCode "git commit failed"
} else {
    Write-Host "No new local source changes to commit."
}

# Incorporate commits made on another device before publishing this device's
# commits. A conflict stops the script; it never force-pushes or drops history.
$RemoteHeads = @(& git ls-remote --heads origin "refs/heads/$Branch")
Assert-LastExitCode "Cannot contact the Gitea repository at $GiteaRepoUrl"
if ($RemoteHeads.Count -gt 0) {
    & git fetch origin $Branch
    Assert-LastExitCode "Failed to fetch the latest Gitea history"
    & git rebase "origin/$Branch"
    Assert-LastExitCode "Rebase stopped. Resolve the conflicts, run 'git rebase --continue', then run this script again"
}

# Normal push to Gitea only. Gitea mirrors it to GitHub for Actions.
& git push origin $Branch
Assert-LastExitCode "Gitea push failed; remote history was left unchanged"

Write-Host "Source synced to Gitea without rewriting history."
Write-Host "Gitea will mirror this commit to GitHub, which will start GitHub Actions."
