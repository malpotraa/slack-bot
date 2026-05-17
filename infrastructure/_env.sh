#!/usr/bin/env bash
# Common config sourced by every infrastructure script.
#
# Edit these values BEFORE running any of the numbered scripts.

set -euo pipefail

# ─── Required ────────────────────────────────────────────────────────────
export PROJECT_ID="${PROJECT_ID:-CHANGE-ME}"        # e.g. "my-slack-assistant"
export REGION="${REGION:-us-central1}"
export SERVICE_NAME="${SERVICE_NAME:-slack-assistant}"

# ─── Cloud SQL ───────────────────────────────────────────────────────────
export SQL_INSTANCE="${SQL_INSTANCE:-${SERVICE_NAME}-pg}"
export SQL_DB_NAME="${SQL_DB_NAME:-slack_assistant}"
export SQL_DB_USER="${SQL_DB_USER:-app}"
export SQL_TIER="${SQL_TIER:-db-f1-micro}"
export SQL_DISK_GB="${SQL_DISK_GB:-10}"
# DB password — generated automatically on first run if blank
export SQL_DB_PASSWORD="${SQL_DB_PASSWORD:-}"

# ─── Artifact Registry ──────────────────────────────────────────────────
export AR_REPO="${AR_REPO:-${SERVICE_NAME}}"

# ─── Service account ────────────────────────────────────────────────────
export RUN_SA="${RUN_SA:-${SERVICE_NAME}-run-sa}"

# ─── Sanity check ────────────────────────────────────────────────────────
if [[ "${PROJECT_ID}" == "CHANGE-ME" ]]; then
  echo "ERROR: edit infrastructure/_env.sh and set PROJECT_ID."
  exit 1
fi

# Helpful derivations the other scripts use
export SQL_CONNECTION_NAME="${PROJECT_ID}:${REGION}:${SQL_INSTANCE}"
export AR_HOST="${REGION}-docker.pkg.dev"
export IMAGE_BASE="${AR_HOST}/${PROJECT_ID}/${AR_REPO}/${SERVICE_NAME}"
