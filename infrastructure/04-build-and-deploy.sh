#!/usr/bin/env bash
# Step 4: First-time build + deploy. Subsequent updates can re-run this script.
#
# Pass --no-build to skip the Cloud Build step (useful when only env vars changed).

set -euo pipefail
cd "$(dirname "$0")/.."     # cd to prod/ root so cloudbuild.yaml is in cwd
source infrastructure/_env.sh

DO_BUILD=1
[[ "${1:-}" == "--no-build" ]] && DO_BUILD=0

# ── 1. Initial deploy needs APP_BASE_URL, but we don't know the URL until
#       Cloud Run creates the service. Strategy:
#         a) deploy with a placeholder APP_BASE_URL
#         b) read the URL Cloud Run assigned
#         c) re-deploy with the real APP_BASE_URL
PLACEHOLDER_URL="https://placeholder.invalid"

RUN_SA_EMAIL="${RUN_SA}@${PROJECT_ID}.iam.gserviceaccount.com"

# Submit the build (which also deploys via the cloudbuild.yaml step)
if [[ $DO_BUILD -eq 1 ]]; then
  echo "▶ Submitting Cloud Build (this takes 4–7 minutes the first time)..."
  gcloud builds submit \
    --config=cloudbuild.yaml \
    --substitutions=_REGION="${REGION}",_SERVICE_NAME="${SERVICE_NAME}",_AR_REPO="${AR_REPO}",_SQL_INSTANCE="${SQL_CONNECTION_NAME}",_APP_BASE_URL="${APP_BASE_URL:-${PLACEHOLDER_URL}}",_RUN_SA_EMAIL="${RUN_SA_EMAIL}" \
    .
fi

# Look up the URL Cloud Run assigned and re-deploy with it
URL="$(gcloud run services describe "${SERVICE_NAME}" --region="${REGION}" --format='value(status.url)' 2>/dev/null || true)"
if [[ -z "${URL}" ]]; then
  echo "ERROR: Cloud Run service URL not found. Did the deploy succeed?"
  exit 1
fi

if [[ "${URL}" != "${APP_BASE_URL:-}" ]]; then
  echo
  echo "▶ Patching APP_BASE_URL to the real Cloud Run URL: ${URL}"
  gcloud run services update "${SERVICE_NAME}" \
    --region="${REGION}" \
    --update-env-vars="APP_BASE_URL=${URL}" >/dev/null
fi

echo
echo "✅ Service deployed."
echo "   URL:          ${URL}"
echo "   Health check: curl ${URL}/healthz"
echo
echo "Next steps (one-time):"
echo "  1. Update Slack OAuth redirect URL → ${URL}/oauth/slack/callback"
echo "  2. Update Google OAuth redirect URI → ${URL}/oauth/google/callback"
echo "  3. Update Wrike OAuth redirect URI → ${URL}/oauth/wrike/callback"
