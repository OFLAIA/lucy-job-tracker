#!/usr/bin/env bash
# Interactive helper to add a new user to the job tracker.
#
# When you receive a Formspree submission email, run this from inside the
# repo and paste in the fields one at a time. It creates users/{id}.json,
# commits, and pushes. The next scheduled run will start delivering to
# that user.
#
# Usage:
#   bash scripts/add_user.sh
#
# Or, non-interactively:
#   FIRST_NAME="Jane" EMAIL="jane@example.com" \
#     LOCATIONS="London,Hybrid - London" \
#     SENIORITY=graduate ROLE_FOCUS="claims and underwriting" \
#     bash scripts/add_user.sh

set -euo pipefail

GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; RED=$'\033[0;31m'; BOLD=$'\033[1m'; NC=$'\033[0m'
say()  { printf "%s==>%s %s\n" "$GREEN" "$NC" "$*"; }
warn() { printf "%s!! %s%s\n"  "$YELLOW" "$*" "$NC"; }
die()  { printf "%sxx %s%s\n"  "$RED"    "$*" "$NC" >&2; exit 1; }

# Must be run from repo root (next to main.py).
[[ -f main.py ]] || die "Run this from the repo root (the folder containing main.py)."

# ── prompt or take from env ────────────────────────────────────────────────────
read_default() {
  local prompt="$1" varname="$2" default="${3:-}"
  local existing="${!varname:-}"
  if [[ -n "$existing" ]]; then
    echo "  $prompt: $existing"
    return
  fi
  if [[ -n "$default" ]]; then
    read -r -p "  $prompt [$default]: " val
    eval "$varname=\"${val:-$default}\""
  else
    read -r -p "  $prompt: " val
    eval "$varname=\"$val\""
  fi
}

say "${BOLD}Add a new user to the job tracker${NC}"
echo

say "Read these from the Formspree email you received:"
read_default "First name" FIRST_NAME
read_default "Email address" EMAIL
read_default "Locations (comma-separated, e.g. 'London,Essex,Remote - UK')" LOCATIONS "London"
read_default "Seniority (school_leaver|graduate|junior|mid|senior)" SENIORITY "school_leaver"
read_default "Role focus (optional, plain English)" ROLE_FOCUS ""

# ── derive the user id from the first name ──────────────────────────────────
SLUG=$(echo "$FIRST_NAME" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '-' | sed 's/-*$//' | sed 's/^-*//')
USER_FILE="users/${SLUG}.json"

# Handle collisions by appending -2, -3, etc.
n=1
while [[ -f "$USER_FILE" ]]; do
  n=$((n+1))
  USER_FILE="users/${SLUG}-${n}.json"
done
[[ $n -gt 1 ]] && warn "Slug ${SLUG} already taken; using ${SLUG}-${n}."
ID=$(basename "$USER_FILE" .json)

# ── convert comma-separated LOCATIONS into a JSON array ────────────────────
LOCATIONS_JSON=$(python3 -c "
import json, sys
parts = [p.strip() for p in '''$LOCATIONS'''.split(',') if p.strip()]
print(json.dumps(parts))
")

CREATED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# ── write the user file ─────────────────────────────────────────────────────
mkdir -p users
cat > "$USER_FILE" <<EOF
{
  "id": "$ID",
  "name": "$FIRST_NAME",
  "email": "$EMAIL",
  "industry": "insurance",
  "region": "uk",
  "locations": $LOCATIONS_JSON,
  "exclude_locations": [],
  "seniority": "$SENIORITY",
  "role_focus": $(python3 -c "import json,sys; print(json.dumps('''$ROLE_FOCUS'''))"),
  "active": true,
  "paused_until": null,
  "created_at": "$CREATED_AT"
}
EOF

say "Wrote ${BOLD}$USER_FILE${NC}"
echo
cat "$USER_FILE"
echo

# ── validate it loads cleanly ──────────────────────────────────────────────
python3 -c "
import json, sys
sys.path.insert(0, '.')
from lib import user as user_mod
with open('$USER_FILE') as f:
    cfg = json.load(f)
errs = user_mod.validate(cfg)
if errs:
    print('VALIDATION FAILED:', errs)
    sys.exit(1)
print('Validation OK.')
"

# ── optionally commit and push ─────────────────────────────────────────────
echo
read -r -p "Commit and push now? [Y/n]: " GO
GO=${GO:-Y}
if [[ "$GO" =~ ^[Yy]$ ]]; then
  git add "$USER_FILE"
  git commit -q -m "Add user: $FIRST_NAME ($ID)"
  git pull --rebase --quiet || true
  git push --quiet
  say "Pushed. They'll be in the next scheduled run."
  echo
  read -r -p "Trigger a run now so they get their first email today? [y/N]: " RUN
  if [[ "${RUN:-N}" =~ ^[Yy]$ ]]; then
    gh workflow run daily.yml
    say "Run triggered. Check Actions tab on GitHub."
  fi
else
  warn "Not committed. Run 'git add $USER_FILE && git commit && git push' when ready."
fi
