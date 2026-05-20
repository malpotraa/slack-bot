#!/usr/bin/env bash
# Add Google Ads KPI schema introduced after the security-hardening migration.
#
# This migration creates the googleadstoken table used by /connect and /kpi.

set -euo pipefail
cd "$(dirname "$0")"
source ./_env.sh

echo "▶ Applying Google Ads KPI migration to:"
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

CREATE TABLE IF NOT EXISTS googleadstoken (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES "user"(id),
    encrypted_refresh_token VARCHAR NOT NULL,
    encrypted_access_token VARCHAR NULL,
    access_token_expires_at TIMESTAMP WITH TIME ZONE NULL,
    scopes VARCHAR NOT NULL DEFAULT '',
    google_email VARCHAR NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

ALTER TABLE "user"
    ADD COLUMN IF NOT EXISTS google_ads_connect_channel_id VARCHAR NULL;

ALTER TABLE "user"
    ADD COLUMN IF NOT EXISTS google_ads_connect_ts VARCHAR NULL;

-- Keep one Google Ads token per user. If an interrupted/manual migration
-- created duplicates, retain the newest row before adding the unique index.
WITH ranked_google_ads_tokens AS (
    SELECT
        id,
        row_number() OVER (
            PARTITION BY user_id
            ORDER BY updated_at DESC NULLS LAST, id DESC
        ) AS rn
    FROM googleadstoken
)
DELETE FROM googleadstoken t
USING ranked_google_ads_tokens r
WHERE t.id = r.id AND r.rn > 1;

CREATE UNIQUE INDEX IF NOT EXISTS ix_googleadstoken_user_id
    ON googleadstoken (user_id);

COMMIT;
SQL

echo
echo "✅ Google Ads KPI migration applied."
