#!/usr/bin/env bash
# Add opt-in daily /goodmorning briefing columns to the user table.
#
# Off by default: a user enables it for themselves via `/goodmorning subscribe`
# or the button on the briefing. `daily_briefing_time` is local HH:MM (24h),
# interpreted in the user's existing `tz`.

set -euo pipefail
cd "$(dirname "$0")"
source ./_env.sh

echo "▶ Applying daily-briefing migration to:"
echo "    instance: ${SQL_INSTANCE}"
echo "    database: ${SQL_DB_NAME}"
echo
read -r -p "Type 'migrate' to continue: " CONFIRM
if [[ "${CONFIRM}" != "migrate" ]]; then
  echo "Aborted."; exit 1
fi

PG_ROOT_PASSWORD_FILE="/tmp/${SERVICE_NAME}-pg-root.pwd"
if [[ ! -f "${PG_ROOT_PASSWORD_FILE}" ]]; then
  echo "ERROR: postgres root password file not found at ${PG_ROOT_PASSWORD_FILE}"
  echo "Set a new one with:"
  echo "  gcloud sql users set-password postgres --instance=${SQL_INSTANCE} --password=NEW"
  exit 1
fi

PGPASSWORD="$(cat "${PG_ROOT_PASSWORD_FILE}")" gcloud sql connect "${SQL_INSTANCE}" \
  --user=postgres \
  --database="${SQL_DB_NAME}" \
  --quiet <<'SQL'
BEGIN;

ALTER TABLE "user"
    ADD COLUMN IF NOT EXISTS daily_briefing_enabled BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE "user"
    ADD COLUMN IF NOT EXISTS daily_briefing_time VARCHAR NOT NULL DEFAULT '08:00';

COMMIT;
SQL

echo
echo "✅ Daily-briefing migration applied."
