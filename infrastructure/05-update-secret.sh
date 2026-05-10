#!/usr/bin/env bash
# Helper: rotate a single secret without re-running the full step 02.
# Usage:  ./05-update-secret.sh SECRET_NAME "new value"

set -euo pipefail
cd "$(dirname "$0")"
source ./_env.sh

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 SECRET_NAME 'new value'"
  exit 1
fi
NAME="$1"; VALUE="$2"

if ! gcloud secrets describe "${NAME}" >/dev/null 2>&1; then
  echo "Creating new secret ${NAME}"
  gcloud secrets create "${NAME}" --replication-policy=automatic >/dev/null
fi
printf '%s' "${VALUE}" | gcloud secrets versions add "${NAME}" --data-file=- >/dev/null
echo "✅ ${NAME} updated. Re-deploy the Cloud Run service to pick up the new value:"
echo "   gcloud run services update ${SERVICE_NAME} --region=${REGION} --update-secrets=${NAME}=${NAME}:latest"
