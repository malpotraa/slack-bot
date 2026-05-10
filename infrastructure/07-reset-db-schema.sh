#!/usr/bin/env bash
# One-shot: DROP and recreate Cloud SQL tables to pick up a schema change.
#
# When to use:
#   - Right after changing column types in app/db/models.py (e.g. switching
#     timestamp columns to TIMESTAMP WITH TIME ZONE).
#
# IMPORTANT: This destroys all data in those tables. The intended use is when
# the DB is empty or the data is throwaway. If you have real data, write a
# proper ALTER TABLE migration instead.

set -euo pipefail
cd "$(dirname "$0")"
source ./_env.sh

echo "▶ This will DROP every application table in:"
echo "    instance: ${SQL_INSTANCE}"
echo "    database: ${SQL_DB_NAME}"
echo
read -r -p "Type 'reset' to confirm: " CONFIRM
if [[ "${CONFIRM}" != "reset" ]]; then
  echo "Aborted."; exit 1
fi

# Use the postgres root credentials saved by 01-create-cloud-sql.sh
PG_ROOT_PASSWORD_FILE="/tmp/${SERVICE_NAME}-pg-root.pwd"
if [[ ! -f "${PG_ROOT_PASSWORD_FILE}" ]]; then
  echo "ERROR: postgres root password file not found at ${PG_ROOT_PASSWORD_FILE}"
  echo "If you've lost it, set a new one with:"
  echo "  gcloud sql users set-password postgres --instance=${SQL_INSTANCE} --password=NEW"
  exit 1
fi

echo
echo "▶ Connecting via Cloud SQL Proxy. Authorize yourself when prompted."
PGPASSWORD="$(cat "${PG_ROOT_PASSWORD_FILE}")" gcloud sql connect "${SQL_INSTANCE}" \
  --user=postgres \
  --database="${SQL_DB_NAME}" \
  --quiet <<'SQL'
DROP TABLE IF EXISTS conversationsession CASCADE;
DROP TABLE IF EXISTS workflowstatuscache CASCADE;
DROP TABLE IF EXISTS slackusertoken CASCADE;
DROP TABLE IF EXISTS wriketoken CASCADE;
DROP TABLE IF EXISTS googletoken CASCADE;
DROP TABLE IF EXISTS "user" CASCADE;
\dt
SQL

echo
echo "✅ Tables dropped. Redeploy to recreate them with the new schema:"
echo "    ./infrastructure/04-build-and-deploy.sh --no-build"
