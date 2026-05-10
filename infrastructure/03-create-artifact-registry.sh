#!/usr/bin/env bash
# Step 3: Create the Artifact Registry Docker repo + the Cloud Run service account.

set -euo pipefail
cd "$(dirname "$0")"
source ./_env.sh

# ── Artifact Registry ────────────────────────────────────────────────────
if gcloud artifacts repositories describe "${AR_REPO}" --location="${REGION}" >/dev/null 2>&1; then
  echo "✓ Artifact Registry repo ${AR_REPO} already exists."
else
  echo "▶ Creating Artifact Registry repo ${AR_REPO} (Docker, ${REGION})"
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Slack Assistant Docker images"
fi

# ── Service account for the Cloud Run service ───────────────────────────
SA_EMAIL="${RUN_SA}@${PROJECT_ID}.iam.gserviceaccount.com"
if gcloud iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1; then
  echo "✓ Service account ${SA_EMAIL} already exists."
else
  echo "▶ Creating service account ${SA_EMAIL}"
  gcloud iam service-accounts create "${RUN_SA}" \
    --display-name="Cloud Run runtime SA for ${SERVICE_NAME}"
fi

# Grant runtime roles
echo "▶ Granting roles to ${SA_EMAIL}"
for role in roles/cloudsql.client roles/secretmanager.secretAccessor roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${role}" \
    --condition=None >/dev/null
done

# Cloud Build needs to deploy + impersonate the runtime SA + read secrets at
# build time. Google changed the default Cloud Build SA model in 2024:
#   - legacy projects: <PROJECT_NUMBER>@cloudbuild.gserviceaccount.com
#   - newer projects: <PROJECT_NUMBER>-compute@developer.gserviceaccount.com
# Grant to BOTH so this works regardless of which one your project uses.
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
CB_SA_LEGACY="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
CB_SA_COMPUTE="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

for SA in "${CB_SA_LEGACY}" "${CB_SA_COMPUTE}"; do
  echo "▶ Granting Cloud Build deploy permissions (${SA})"
  for role in roles/run.admin roles/iam.serviceAccountUser roles/secretmanager.secretAccessor roles/artifactregistry.writer roles/logging.logWriter; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${SA}" \
      --role="${role}" \
      --condition=None >/dev/null 2>&1 || true
  done
done

echo
echo "✅ Artifact Registry + service accounts ready."
echo "   Run SA:                ${SA_EMAIL}"
echo "   Cloud Build SA legacy: ${CB_SA_LEGACY}"
echo "   Cloud Build SA compute:${CB_SA_COMPUTE}"
