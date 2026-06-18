#!/usr/bin/env bash
# DANGER: removes everything this project created in your GCP project.
# Run only if you want to fully delete the deployment.

set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE="${ENV_FILE:-./_env.sh}"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: env file not found: ${ENV_FILE}"
  exit 1
fi
source "${ENV_FILE}"

read -r -p "Type the project ID '${PROJECT_ID}' to confirm tear-down: " CONFIRM
if [[ "${CONFIRM}" != "${PROJECT_ID}" ]]; then
  echo "Aborted."; exit 1
fi

echo "▶ Deleting Cloud Run service ${SERVICE_NAME}..."
gcloud run services delete "${SERVICE_NAME}" --region="${REGION}" --quiet || true

echo "▶ Deleting Artifact Registry repo..."
gcloud artifacts repositories delete "${AR_REPO}" --location="${REGION}" --quiet || true

echo "▶ Deleting Cloud SQL instance (this is irreversible)..."
gcloud sql instances delete "${SQL_INSTANCE}" --quiet || true

echo "▶ Deleting Secret Manager secrets..."
for s in APP_SECRET_KEY TOKEN_ENCRYPTION_KEY DATABASE_URL ANTHROPIC_API_KEY \
         SLACK_BOT_TOKEN SLACK_APP_TOKEN SLACK_SIGNING_SECRET SLACK_CLIENT_ID SLACK_CLIENT_SECRET \
         GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET WRIKE_CLIENT_ID WRIKE_CLIENT_SECRET BRAINTRUST_API_KEY; do
  gcloud secrets delete "$s" --quiet 2>/dev/null || true
done

echo "▶ Deleting service account..."
SA_EMAIL="${RUN_SA}@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud iam service-accounts delete "${SA_EMAIL}" --quiet || true

echo "✅ Tear-down complete."
