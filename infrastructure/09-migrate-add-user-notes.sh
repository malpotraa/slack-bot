#!/usr/bin/env bash
# One-shot migration: add the `notes` column to the user table.
# Free-form preferences the assistant remembers across conversations.
# Safe to re-run — uses ADD COLUMN IF NOT EXISTS.

set -euo pipefail
cd "$(dirname "$0")"
source ./_env.sh

echo "▶ Adding notes (TEXT NULL) to ${SQL_DB_NAME}.user"
echo
echo "CRITICAL: connect as the 'app' user (not postgres) because 'app' owns"
echo "the table. The postgres role only has cloudsqlsuperuser, not true owner."
echo
echo "Run this in Cloud Shell (or wherever 'gcloud sql connect' works):"
echo
cat <<'EOF'
gcloud sql connect slack-assistant-pg --user=app --database=slack_assistant
EOF
echo
echo "Then paste:"
echo
cat <<'EOF'
ALTER TABLE "user" ADD COLUMN IF NOT EXISTS notes TEXT;
\d "user"
\q
EOF
