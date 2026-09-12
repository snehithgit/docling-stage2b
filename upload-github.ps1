$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Repo = "snehithgit/docling-stage2b"
$Branch = "main"

Write-Host "======================================"
Write-Host " Docling Stage2B GitHub Publisher"
Write-Host "======================================"
Write-Host ""
Write-Host "Repository: $Repo"
Write-Host "Directory : $(Get-Location)"
Write-Host ""

function Assert-LastExitCode {
    param([string]$Message)
    if ($LASTEXITCODE -ne 0) {
        throw "$Message (exit code $LASTEXITCODE)"
    }
}

# Must be run from the project root.
if (-not (Test-Path -LiteralPath ".\Dockerfile" -PathType Leaf) -or
    -not (Test-Path -LiteralPath ".\app" -PathType Container)) {
    throw "Run this script from the docling-stage2b project root."
}

# Check Git.
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is not installed or not in PATH. Install Git for Windows first."
}

# Check GitHub CLI.
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) is not installed or not in PATH. Install it first."
}

# Check GitHub authentication.
& gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI is not authenticated. Run: gh auth login"
}

# Configure Git to use the GitHub CLI credential helper.
& gh auth setup-git
Assert-LastExitCode "Failed to configure GitHub authentication for Git"

# Remove only obsolete bootstrap artifacts from the earlier failed upload method.
if (Test-Path -LiteralPath ".\buildsrc") {
    Remove-Item -LiteralPath ".\buildsrc" -Recurse -Force
}
if (Test-Path -LiteralPath ".\source") {
    Remove-Item -LiteralPath ".\source" -Recurse -Force
}
if (Test-Path -LiteralPath ".\runtime.tar.xz") {
    Remove-Item -LiteralPath ".\runtime.tar.xz" -Force
}

# Recreate local Git metadata so this project tree can replace the old repo tree.
if (Test-Path -LiteralPath ".\.git") {
    Remove-Item -LiteralPath ".\.git" -Recurse -Force
}

& git init
Assert-LastExitCode "git init failed"

& git branch -M $Branch
Assert-LastExitCode "Failed to set branch to $Branch"

$Login = (& gh api user --jq ".login").Trim()
Assert-LastExitCode "Failed to read GitHub login"

$UserId = (& gh api user --jq ".id").Trim()
Assert-LastExitCode "Failed to read GitHub user ID"

& git config user.name $Login
Assert-LastExitCode "Failed to configure git user.name"

& git config user.email "$UserId+$Login@users.noreply.github.com"
Assert-LastExitCode "Failed to configure git user.email"

& git add .
Assert-LastExitCode "git add failed"

Write-Host ""
Write-Host "Files/changes to publish:"
& git status --short
Assert-LastExitCode "git status failed"

& git commit -m "Update Stage 2C imported-book backfill and document library"
Assert-LastExitCode "git commit failed"

& git remote add origin "https://github.com/$Repo.git"
Assert-LastExitCode "Failed to add GitHub remote"

Write-Host ""
Write-Host "Replacing GitHub main branch with this project tree..."
& git push --force origin $Branch
Assert-LastExitCode "GitHub push failed"

Write-Host ""
Write-Host "======================================"
Write-Host " Source uploaded successfully"
Write-Host "======================================"
Write-Host ""
Write-Host "Repository:"
Write-Host "https://github.com/$Repo"
Write-Host ""
Write-Host "Docker image:"
Write-Host "ghcr.io/$Repo`:latest"
Write-Host ""
Write-Host "Waiting for workflow information..."

Start-Sleep -Seconds 3

& gh run list --repo $Repo --workflow docker-publish.yml --limit 3
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Could not list workflow runs yet. The push itself succeeded."
}

Write-Host ""
Write-Host "Watch the newest build with:"
Write-Host "gh run watch --repo $Repo"
