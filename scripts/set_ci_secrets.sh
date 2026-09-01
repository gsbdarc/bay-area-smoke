#!/usr/bin/env bash
# Push the EPA AQS credentials from .env into GitHub Actions secrets.
#
# The key lives in exactly three places, and this script handles the third:
#   - your machine:  .env (gitignored, chmod 600)
#   - the server:    .env in the clone
#   - CI:            repo Actions secrets  <-- this script
#
# Without the CI copy the monthly refresh workflow fails at stage s02, which
# refuses to build a dataset that silently stops at the last certified year.
#
# Values are read from .env and passed to `gh` directly. Nothing is echoed, so
# this is safe to run inside a shared session or a recorded terminal.
#
# Usage:  bash scripts/set_ci_secrets.sh [owner/repo]

set -euo pipefail

REPO="${1:-gsbdarc/bay-area-smoke}"
ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "No .env at $ENV_FILE" >&2
  echo "Create it first -- see docs/SERVER-SETUP.md section 3." >&2
  exit 1
fi

# Read without printing. `cut -d= -f2-` keeps any '=' inside the value.
get() {
  local key="$1" val
  val="$(grep -m1 "^${key}=" "$ENV_FILE" | cut -d= -f2- || true)"
  # Strip surrounding quotes and whitespace if present.
  val="${val%\"}"; val="${val#\"}"
  val="${val%\'}"; val="${val#\'}"
  printf '%s' "$val" | tr -d '[:space:]'
}

fail=0
for key in AQS_EMAIL AQS_KEY; do
  value="$(get "$key")"
  if [[ -z "$value" ]]; then
    echo "  $key: MISSING from .env -- skipped" >&2
    fail=1
    continue
  fi
  gh secret set "$key" --repo "$REPO" --body "$value"
  echo "  $key: set (${#value} characters, value not shown)"
done

[[ $fail -eq 0 ]] || { echo "One or more secrets were missing." >&2; exit 1; }

echo
echo "Secrets now on $REPO (names and timestamps only):"
gh secret list --repo "$REPO"

echo
echo "Next: gh workflow run refresh.yml --repo $REPO"
