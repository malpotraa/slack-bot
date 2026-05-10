#!/usr/bin/env bash
# Step 2: Create every secret in Secret Manager and store its value.
#
# This script reads values from a key=value file you provide
# (default: infrastructure/secrets.env, which is .gitignored). Edit that file
# before running.
#
# Required keys in secrets.env:
#   APP_SECRET_KEY            (generate with scripts/gen_keys.py)
#   TOKEN_ENCRYPTION_KEY      (generate with scripts/gen_keys.py)
#   ANTHROPIC_API_KEY
#   SLACK_BOT_TOKEN  SLACK_APP_TOKEN  SLACK_SIGNING_SECRET
#   SLACK_CLIENT_ID  SLACK_CLIENT_SECRET
#   GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET
#   WRIKE_CLIENT_ID  WRIKE_CLIENT_SECRET
#   PHOENIX_API_KEY
#   DATABASE_URL              (printed by 01-create-cloud-sql.sh)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROD_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${SCRIPT_DIR}"
source ./_env.sh

# Default location: prod/infrastructure-secrets.env (alongside pyproject.toml).
# An explicit path can still be passed as the first argument.
DEFAULT_PATH="${PROD_ROOT}/infrastructure-secrets.env"
SECRETS_FILE="${1:-${DEFAULT_PATH}}"

if [[ ! -f "${SECRETS_FILE}" ]]; then
  echo "ERROR: secrets file not found at ${SECRETS_FILE}"
  echo
  echo "Create it from infrastructure/secrets.env.example, then re-run:"
  echo "  cd ${PROD_ROOT}"
  echo "  cp infrastructure/secrets.env.example infrastructure-secrets.env"
  echo "  # …edit infrastructure-secrets.env, fill in every value…"
  echo "  ./infrastructure/02-create-secrets.sh"
  exit 1
fi

# Source it. Lines look like KEY="value"
set -a
# shellcheck disable=SC1090
source "${SECRETS_FILE}"
set +a

SECRETS=(
  APP_SECRET_KEY
  TOKEN_ENCRYPTION_KEY
  DATABASE_URL
  ANTHROPIC_API_KEY
  SLACK_BOT_TOKEN
  SLACK_APP_TOKEN
  SLACK_SIGNING_SECRET
  SLACK_CLIENT_ID
  SLACK_CLIENT_SECRET
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET
  WRIKE_CLIENT_ID
  WRIKE_CLIENT_SECRET
  PHOENIX_API_KEY
)

for key in "${SECRETS[@]}"; do
  val="${!key:-}"
  if [[ -z "${val}" ]]; then
    echo "  ⚠ skipping ${key} — no value in ${SECRETS_FILE}"
    continue
  fi
  if gcloud secrets describe "${key}" >/dev/null 2>&1; then
    echo "  ↻ updating ${key}"
  else
    echo "  + creating ${key}"
    gcloud secrets create "${key}" --replication-policy=automatic >/dev/null
  fi
  printf '%s' "${val}" | gcloud secrets versions add "${key}" --data-file=- >/dev/null
done

echo
echo "✅ Secrets uploaded to Secret Manager."
echo "   Verify: gcloud secrets list --filter='name~(APP_SECRET_KEY|DATABASE_URL|SLACK_)'"
