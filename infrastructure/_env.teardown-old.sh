#!/usr/bin/env bash
# Environment values for tearing down the old pre-Pronto deployment.
#
# Use with:
#   ENV_FILE=./_env.teardown-old.sh ./99-tear-down.sh

set -euo pipefail

export PROJECT_ID="${PROJECT_ID:-slack-marketing-bot}"
export REGION="${REGION:-us-central1}"
export SERVICE_NAME="${SERVICE_NAME:-slack-assistant}"

export SQL_INSTANCE="${SQL_INSTANCE:-slack-assistant-pg}"
export SQL_DB_NAME="${SQL_DB_NAME:-slack_assistant}"
export SQL_DB_USER="${SQL_DB_USER:-app}"

export AR_REPO="${AR_REPO:-slack-assistant}"
export RUN_SA="${RUN_SA:-slack-assistant-run-sa}"

export SQL_CONNECTION_NAME="${PROJECT_ID}:${REGION}:${SQL_INSTANCE}"
export AR_HOST="${REGION}-docker.pkg.dev"
export IMAGE_BASE="${AR_HOST}/${PROJECT_ID}/${AR_REPO}/${SERVICE_NAME}"
