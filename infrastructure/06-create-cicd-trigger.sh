#!/usr/bin/env bash
# Step 6: Create the Cloud Build trigger that auto-deploys on every push to GitHub.
#
# PREREQUISITES (do these manually first):
#   1. Push your code to a GitHub repo.
#   2. Install the "Google Cloud Build" GitHub App on the repo:
#        https://github.com/marketplace/google-cloud-build
#      Pick "Only select repositories" and choose your repo.
#   3. In GCP Console, connect the repo to Cloud Build:
#        https://console.cloud.google.com/cloud-build/triggers/connect
#      Pick GitHub (Cloud Build GitHub App) → choose your repo → Connect.
#
# Then run this script. It creates / updates the trigger.

set -euo pipefail
cd "$(dirname "$0")"
source ./_env.sh

# ── Required: set these before running ───────────────────────────────────
GITHUB_OWNER="${GITHUB_OWNER:-}"          # e.g. "abhishekmalpotra"
GITHUB_REPO="${GITHUB_REPO:-}"            # e.g. "slack-assistant" (just the repo name)
GITHUB_BRANCH="${GITHUB_BRANCH:-^main$}"  # regex; default = main branch only
TRIGGER_NAME="${TRIGGER_NAME:-${SERVICE_NAME}-deploy}"
APP_BASE_URL="${APP_BASE_URL:-}"          # e.g. https://slack-assistant-abcd-uc.a.run.app
RUN_SA_EMAIL="${RUN_SA}@${PROJECT_ID}.iam.gserviceaccount.com"

if [[ -z "${GITHUB_OWNER}" || -z "${GITHUB_REPO}" || -z "${APP_BASE_URL}" ]]; then
  cat <<EOF
Set GITHUB_OWNER, GITHUB_REPO, and APP_BASE_URL before running. Example:

  export GITHUB_OWNER="your-github-username"
  export GITHUB_REPO="slack-assistant"
  export APP_BASE_URL="\$(gcloud run services describe ${SERVICE_NAME} --region=${REGION} --format='value(status.url)')"
  ./infrastructure/06-create-cicd-trigger.sh
EOF
  exit 1
fi

# ── Create or update the trigger ─────────────────────────────────────────
echo "▶ Creating/updating Cloud Build trigger '${TRIGGER_NAME}'"
echo "    Repo:       ${GITHUB_OWNER}/${GITHUB_REPO}"
echo "    Branch:     ${GITHUB_BRANCH}"
echo "    Build cfg:  prod/cloudbuild.yaml"
echo "    Region:     ${REGION}"

# `gcloud builds triggers create github` is idempotent-via-replace if you delete + recreate;
# we delete first if it exists.
if gcloud builds triggers describe "${TRIGGER_NAME}" --region="${REGION}" >/dev/null 2>&1; then
  echo "    (replacing existing trigger with the same name)"
  gcloud builds triggers delete "${TRIGGER_NAME}" --region="${REGION}" --quiet
fi

gcloud builds triggers create github \
  --name="${TRIGGER_NAME}" \
  --region="${REGION}" \
  --repo-owner="${GITHUB_OWNER}" \
  --repo-name="${GITHUB_REPO}" \
  --branch-pattern="${GITHUB_BRANCH}" \
  --build-config="prod/cloudbuild.yaml" \
  --included-files="prod/**" \
  --substitutions="_REGION=${REGION},_SERVICE_NAME=${SERVICE_NAME},_AR_REPO=${AR_REPO},_SQL_INSTANCE=${SQL_CONNECTION_NAME},_APP_BASE_URL=${APP_BASE_URL},_RUN_SA_EMAIL=${RUN_SA_EMAIL}" \
  --description="Auto-deploy ${SERVICE_NAME} from main on prod/** changes"

echo
echo "✅ Trigger created. View at:"
echo "    https://console.cloud.google.com/cloud-build/triggers?project=${PROJECT_ID}"
echo
echo "Next: make any small change to a prod/ file, commit + push to main, and watch:"
echo "    https://console.cloud.google.com/cloud-build/builds?project=${PROJECT_ID}"
