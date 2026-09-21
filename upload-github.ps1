$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Repo = "snehithgit/docling-stage2b"
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
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw "GitHub CLI (gh) is not installed or not in PATH." }
& gh auth status *> $null
if ($LASTEXITCODE -ne 0) { throw "GitHub CLI is not authenticated. Run: gh auth login" }
& gh auth setup-git
Assert-LastExitCode "Failed to configure GitHub authentication for Git"

# Remove only obsolete build-bootstrap artifacts. Never delete .git.
foreach ($Path in @(".\buildsrc", ".\source")) {
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Recurse -Force }
}
if (Test-Path -LiteralPath ".\runtime.tar.xz") { Remove-Item -LiteralPath ".\runtime.tar.xz" -Force }

if (-not (Test-Path -LiteralPath ".\.git" -PathType Container)) {
    & git init
    Assert-LastExitCode "git init failed"
    & git remote add origin "https://github.com/$Repo.git"
    Assert-LastExitCode "Failed to add GitHub remote"
    & git fetch origin $Branch *> $null
    if ($LASTEXITCODE -eq 0) {
        & git update-ref "refs/heads/$Branch" FETCH_HEAD
        Assert-LastExitCode "Failed to attach local branch to existing GitHub history"
        & git symbolic-ref HEAD "refs/heads/$Branch"
        Assert-LastExitCode "Failed to select branch"
        & git reset --mixed "refs/heads/$Branch" *> $null
        Assert-LastExitCode "Failed to prepare existing GitHub history"
    } else {
        & git branch -M $Branch
        Assert-LastExitCode "Failed to create branch $Branch"
    }
} else {
    $CurrentBranch = (& git branch --show-current).Trim()
    Assert-LastExitCode "Failed to read current Git branch"
    if ($CurrentBranch -ne $Branch) { throw "Current branch is '$CurrentBranch'. Checkout '$Branch' before publishing." }
    & git remote get-url origin *> $null
    if ($LASTEXITCODE -eq 0) {
        & git remote set-url origin "https://github.com/$Repo.git"
        Assert-LastExitCode "Failed to update GitHub remote"
    } else {
        & git remote add origin "https://github.com/$Repo.git"
        Assert-LastExitCode "Failed to add GitHub remote"
    }
}

$Login = (& gh api user --jq ".login").Trim()
Assert-LastExitCode "Failed to read GitHub login"
$UserId = (& gh api user --jq ".id").Trim()
Assert-LastExitCode "Failed to read GitHub user ID"
& git config user.name $Login
& git config user.email "$UserId+$Login@users.noreply.github.com"

& git add -A
Assert-LastExitCode "git add failed"

$Forbidden = '(^|/)(\.env($|\.)|data/|input/|converted/|processed/|embedding-cache/|[^/]+\.(db|sqlite|sqlite3)($|-))'
$Allowed = @(".env.example")
$Tracked = @(& git ls-files)
Assert-LastExitCode "Failed to inspect tracked files"
$TrackedUnsafe = @($Tracked | Where-Object { $_ -match $Forbidden -and $Allowed -notcontains $_ })
if ($TrackedUnsafe.Count -gt 0) {
    Write-Error ("Refusing to publish because sensitive/runtime files are already tracked:`n" + ($TrackedUnsafe -join "`n"))
}
$Staged = @(& git diff --cached --name-only)
Assert-LastExitCode "Failed to inspect staged files"
$Unsafe = @($Staged | Where-Object { $_ -match $Forbidden -and $Allowed -notcontains $_ })
if ($Unsafe.Count -gt 0) {
    Write-Error ("Refusing to publish because sensitive/runtime files are staged:`n" + ($Unsafe -join "`n"))
}

Write-Host "Files/changes to publish:"
& git status --short
Assert-LastExitCode "git status failed"
& git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "No source changes to publish."
    exit 0
}

& git commit -m $CommitMessage
Assert-LastExitCode "git commit failed"
# Normal push only. Never force-push from this publisher.
& git push origin $Branch
Assert-LastExitCode "GitHub push failed; remote history was left unchanged"

Write-Host "Source uploaded without rewriting Git history."
Start-Sleep -Seconds 3
& gh run list --repo $Repo --workflow docker-publish.yml --limit 3
if ($LASTEXITCODE -ne 0) { Write-Warning "Could not list workflow runs yet. The push itself succeeded." }
