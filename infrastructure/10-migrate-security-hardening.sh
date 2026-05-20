#!/usr/bin/env bash
# Add security-hardening schema changes introduced after initial deployment.
#
# This migration:
#   - creates the approvalexecution table used to make Slack approval cards one-time
#   - adds uniqueness for Slack users, conversation threads, and Wrike status cache rows
#
# The DDL runs in one transaction and deduplicates legacy rows before adding
# unique indexes. If anything fails, the whole migration rolls back.

set -euo pipefail
cd "$(dirname "$0")"
source ./_env.sh

echo "▶ Applying security-hardening migration to:"
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

CREATE TABLE IF NOT EXISTS approvalexecution (
    id SERIAL PRIMARY KEY,
    approval_key VARCHAR NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES "user"(id),
    slack_team_id VARCHAR NOT NULL,
    slack_user_id VARCHAR NOT NULL,
    channel_id VARCHAR NOT NULL,
    message_ts VARCHAR NOT NULL,
    action_id VARCHAR NOT NULL,
    tool_name VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'claimed',
    error VARCHAR NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

-- Merge duplicate Slack user rows before enforcing identity uniqueness.
CREATE TEMP TABLE duplicate_user_map ON COMMIT DROP AS
SELECT id AS loser_id, keeper_id
FROM (
    SELECT
        id,
        first_value(id) OVER (
            PARTITION BY slack_team_id, slack_user_id
            ORDER BY updated_at DESC NULLS LAST, id DESC
        ) AS keeper_id
    FROM "user"
) ranked
WHERE id <> keeper_id;

DELETE FROM googletoken t
USING duplicate_user_map m
WHERE t.user_id = m.loser_id
  AND EXISTS (SELECT 1 FROM googletoken k WHERE k.user_id = m.keeper_id);

UPDATE googletoken t
SET user_id = m.keeper_id
FROM duplicate_user_map m
WHERE t.user_id = m.loser_id;

DO $$
BEGIN
  IF to_regclass('public.googleadstoken') IS NOT NULL THEN
    DELETE FROM googleadstoken t
    USING duplicate_user_map m
    WHERE t.user_id = m.loser_id
      AND EXISTS (SELECT 1 FROM googleadstoken k WHERE k.user_id = m.keeper_id);

    UPDATE googleadstoken t
    SET user_id = m.keeper_id
    FROM duplicate_user_map m
    WHERE t.user_id = m.loser_id;
  END IF;
END $$;

DELETE FROM wriketoken t
USING duplicate_user_map m
WHERE t.user_id = m.loser_id
  AND EXISTS (SELECT 1 FROM wriketoken k WHERE k.user_id = m.keeper_id);

UPDATE wriketoken t
SET user_id = m.keeper_id
FROM duplicate_user_map m
WHERE t.user_id = m.loser_id;

DELETE FROM slackusertoken t
USING duplicate_user_map m
WHERE t.user_id = m.loser_id
  AND EXISTS (SELECT 1 FROM slackusertoken k WHERE k.user_id = m.keeper_id);

UPDATE slackusertoken t
SET user_id = m.keeper_id
FROM duplicate_user_map m
WHERE t.user_id = m.loser_id;

UPDATE conversationsession s
SET user_id = m.keeper_id
FROM duplicate_user_map m
WHERE s.user_id = m.loser_id;

UPDATE workflowstatuscache w
SET user_id = m.keeper_id
FROM duplicate_user_map m
WHERE w.user_id = m.loser_id;

UPDATE approvalexecution a
SET user_id = m.keeper_id
FROM duplicate_user_map m
WHERE a.user_id = m.loser_id;

DELETE FROM "user" u
USING duplicate_user_map m
WHERE u.id = m.loser_id;

-- Keep the newest row for duplicate thread sessions and status-cache entries.
WITH ranked_sessions AS (
    SELECT
        id,
        row_number() OVER (
            PARTITION BY user_id, channel_id, thread_ts
            ORDER BY updated_at DESC NULLS LAST, id DESC
        ) AS rn
    FROM conversationsession
)
DELETE FROM conversationsession s
USING ranked_sessions r
WHERE s.id = r.id AND r.rn > 1;

WITH ranked_status_cache AS (
    SELECT
        id,
        row_number() OVER (
            PARTITION BY user_id, status_name, custom_status_id
            ORDER BY cached_at DESC NULLS LAST, id DESC
        ) AS rn
    FROM workflowstatuscache
)
DELETE FROM workflowstatuscache w
USING ranked_status_cache r
WHERE w.id = r.id AND r.rn > 1;

CREATE INDEX IF NOT EXISTS ix_approvalexecution_approval_key ON approvalexecution (approval_key);
CREATE INDEX IF NOT EXISTS ix_approvalexecution_user_id ON approvalexecution (user_id);
CREATE INDEX IF NOT EXISTS ix_approvalexecution_slack_team_id ON approvalexecution (slack_team_id);
CREATE INDEX IF NOT EXISTS ix_approvalexecution_slack_user_id ON approvalexecution (slack_user_id);
CREATE INDEX IF NOT EXISTS ix_approvalexecution_channel_id ON approvalexecution (channel_id);
CREATE INDEX IF NOT EXISTS ix_approvalexecution_message_ts ON approvalexecution (message_ts);

CREATE UNIQUE INDEX IF NOT EXISTS uq_user_slack_identity
    ON "user" (slack_team_id, slack_user_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_session_thread
    ON conversationsession (user_id, channel_id, thread_ts);

CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_status_cache_entry
    ON workflowstatuscache (user_id, status_name, custom_status_id);

COMMIT;
SQL

echo
echo "✅ Security-hardening migration applied."
