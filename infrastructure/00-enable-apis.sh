#!/usr/bin/env bash
# Step 0: Enable every GCP API the project will use.
# Safe to re-run; APIs already enabled are a no-op.

set -euo pipefail
cd "$(dirname "$0")"
source ./_env.sh

echo "▶ Setting active project: ${PROJECT_ID}"
gcloud config set project "${PROJECT_ID}"

echo "▶ Enabling required APIs (this can take a minute) ..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  compute.googleapis.com \
  servicenetworking.googleapis.com

echo "✅ APIs enabled."
