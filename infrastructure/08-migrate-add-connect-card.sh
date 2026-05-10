#!/usr/bin/env bash
# One-shot migration: add the connect_card_* columns to the user table.
# Safe to re-run — uses ADD COLUMN IF NOT EXISTS.

set -euo pipefail
cd "$(dirname "$0")"
source ./_env.sh

echo "▶ Adding connect_card_channel_id, connect_card_ts to ${SQL_DB_NAME}.user"
echo
echo "Run this in Cloud Shell (or wherever 'gcloud sql connect' works):"
echo
cat <<'EOF'
gcloud sql connect slack-assistant-pg --user=postgres --database=slack_assistant
EOF
echo
echo "Then paste:"
echo
cat <<'EOF'
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS connect_card_channel_id VARCHAR;
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS connect_card_ts VARCHAR;
\d "user"
\q
EOF
