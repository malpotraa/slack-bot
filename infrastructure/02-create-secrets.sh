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
#   GOOGLE_ADS_DEVELOPER_TOKEN GOOGLE_ADS_LOGIN_CUSTOMER_ID
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

RUN_SA_EMAIL="${RUN_SA}@${PROJECT_ID}.iam.gserviceaccount.com"
if ! gcloud iam service-accounts describe "${RUN_SA_EMAIL}" >/dev/null 2>&1; then
  echo "ERROR: runtime service account ${RUN_SA_EMAIL} does not exist."
  echo "Run ./infrastructure/03-create-artifact-registry.sh before this script."
  exit 1
fi

SECRETS=(
  PRONTO_APP_SECRET_KEY
  PRONTO_TOKEN_ENCRYPTION_KEY
  PRONTO_DATABASE_URL
  PRONTO_ANTHROPIC_API_KEY
  PRONTO_SLACK_BOT_TOKEN
  PRONTO_SLACK_APP_TOKEN
  PRONTO_SLACK_SIGNING_SECRET
  PRONTO_SLACK_CLIENT_ID
  PRONTO_SLACK_CLIENT_SECRET
  PRONTO_GOOGLE_CLIENT_ID
  PRONTO_GOOGLE_CLIENT_SECRET
  PRONTO_GOOGLE_ADS_DEVELOPER_TOKEN
  PRONTO_GOOGLE_ADS_LOGIN_CUSTOMER_ID
  PRONTO_WRIKE_CLIENT_ID
  PRONTO_WRIKE_CLIENT_SECRET
  PRONTO_PHOENIX_API_KEY
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
  gcloud secrets add-iam-policy-binding "${key}" \
    --member="serviceAccount:${RUN_SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" >/dev/null
done

echo
echo "✅ Secrets uploaded to Secret Manager."
echo "   Verify: gcloud secrets list --filter='name~(PRONTO_APP_SECRET_KEY|PRONTO_DATABASE_URL|PRONTO_GOOGLE_ADS|PRONTO_SLACK_)'"
