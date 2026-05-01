#!/usr/bin/env bash
# Lucy Job Tracker — one-shot GitHub setup.
#
# What this does, in order:
#   1. Verifies gh (GitHub CLI) and git are installed; offers to install gh via brew if missing.
#   2. Logs you in to GitHub (browser pop-up, takes 30 seconds).
#   3. Creates a new private repo called lucy-job-tracker under your account.
#   4. Initialises this folder as a git repo, commits everything, pushes.
#   5. Reads the six values from .env and uploads them as repo secrets.
#   6. Triggers the first workflow run so Lucy gets her digest within ~2 minutes.
#
# Run this from inside the lucy_job_tracker folder:
#   bash setup_github.sh
#
# Safe to re-run: every step checks state first and skips work that's already done.

set -euo pipefail

REPO_NAME="lucy-job-tracker"
WORKFLOW_FILE="daily.yml"

# ── colours for readable output ────────────────────────────────────────────────
GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; RED=$'\033[0;31m'; BOLD=$'\033[1m'; NC=$'\033[0m'
say()  { printf "%s==>%s %s\n"   "$GREEN"  "$NC" "$*"; }
warn() { printf "%s!! %s%s\n"    "$YELLOW" "$*"  "$NC"; }
die()  { printf "%sxx %s%s\n"    "$RED"    "$*"  "$NC" >&2; exit 1; }

# ── 0. We must be inside the project folder ──────────────────────────────────
[[ -f "main.py" && -d ".github/workflows" ]] || \
  die "Run this from inside the lucy_job_tracker folder (the one containing main.py)."

[[ -f ".env" ]] || die ".env not found — that's where the secrets are read from."

# ── 1. Tooling ────────────────────────────────────────────────────────────────
if ! command -v git >/dev/null 2>&1; then
  die "git is not installed. Install Xcode command line tools first: xcode-select --install"
fi

if ! command -v gh >/dev/null 2>&1; then
  warn "GitHub CLI (gh) not found."
  if command -v brew >/dev/null 2>&1; then
    say "Installing gh via Homebrew (this takes a minute)…"
    brew install gh
  else
    die "Homebrew not installed either. Install brew from https://brew.sh, then re-run this script."
  fi
fi

say "git: $(git --version)"
say "gh:  $(gh --version | head -n1)"

# ── 2. GitHub auth ────────────────────────────────────────────────────────────
if ! gh auth status >/dev/null 2>&1; then
  say "Logging in to GitHub. A browser window will open — pick HTTPS, paste the one-time code."
  gh auth login --hostname github.com --git-protocol https --web
fi

GH_USER=$(gh api user -q .login)
say "Authed as ${BOLD}${GH_USER}${NC}"

# ── 3. Create (or reuse) the private repo ────────────────────────────────────
if gh repo view "${GH_USER}/${REPO_NAME}" >/dev/null 2>&1; then
  warn "Repo ${GH_USER}/${REPO_NAME} already exists — reusing it."
else
  say "Creating private repo ${GH_USER}/${REPO_NAME}…"
  gh repo create "${REPO_NAME}" --private --description "Daily insurance-job digest for Lucy" >/dev/null
fi

# ── 4. Git init / commit / push ──────────────────────────────────────────────
if [[ ! -d ".git" ]]; then
  say "Initialising local git repo…"
  git init -q
  git branch -M main
fi

# Make sure .env will never be committed, even if someone removes the gitignore line.
git rm -r --cached .env >/dev/null 2>&1 || true

# Configure a commit identity if the user hasn't set one globally.
git config user.email >/dev/null 2>&1 || git config user.email "${GH_USER}@users.noreply.github.com"
git config user.name  >/dev/null 2>&1 || git config user.name  "${GH_USER}"

git add .
if git diff --staged --quiet; then
  warn "Nothing new to commit."
else
  git commit -q -m "Initial commit"
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  git remote add origin "https://github.com/${GH_USER}/${REPO_NAME}.git"
fi

say "Pushing to GitHub…"
git push -u origin main

# ── 5. Secrets ────────────────────────────────────────────────────────────────
say "Uploading secrets from .env…"

# Pull values out of .env without sourcing it (avoids surprises with quoting).
get_env_var() {
  local key="$1"
  local val
  val=$(grep -E "^${key}=" .env | head -n1 | cut -d= -f2- | sed -E 's/^"(.*)"$/\1/' | sed -E "s/^'(.*)'$/\1/")
  printf "%s" "$val"
}

set_secret() {
  local key="$1"
  local val
  val=$(get_env_var "$key")
  if [[ -z "$val" ]]; then
    warn "  $key is empty in .env — skipping. You'll need to add it manually."
    return
  fi
  printf "%s" "$val" | gh secret set "$key" --repo "${GH_USER}/${REPO_NAME}" >/dev/null
  say "  $key set"
}

set_secret RESEND_API_KEY
set_secret ANTHROPIC_API_KEY
set_secret LUCY_EMAIL
set_secret LUCY_NAME
set_secret RECIPIENT_EMAIL
set_secret RECIPIENT_NAME

# ── 6. Trigger the first run ─────────────────────────────────────────────────
say "Triggering the first run…"
# Give GitHub a couple of seconds to register the workflow file.
sleep 3
gh workflow run "${WORKFLOW_FILE}" --repo "${GH_USER}/${REPO_NAME}"

say "Watching the run (Ctrl-C to stop watching; the run continues either way)…"
sleep 4
RUN_ID=$(gh run list --repo "${GH_USER}/${REPO_NAME}" --workflow "${WORKFLOW_FILE}" --limit 1 --json databaseId -q '.[0].databaseId')
gh run watch "${RUN_ID}" --repo "${GH_USER}/${REPO_NAME}" || true

echo
say "${BOLD}Done.${NC}"
echo "  Repo:    https://github.com/${GH_USER}/${REPO_NAME}"
echo "  Actions: https://github.com/${GH_USER}/${REPO_NAME}/actions"
echo
echo "If the run was green, Lucy's first digest is on its way to her inbox."
echo "Remind her to check Gmail's Promotions/Spam tab the first time and mark it as 'Move to Inbox'."
