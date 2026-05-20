#!/usr/bin/env bash
# Step 1: Create the Cloud SQL Postgres instance, database, and app user.
#
# Costs: db-f1-micro shared-core ≈ $7/mo + ~$1/mo for 10GB SSD storage.
#        Postgres 15 is fine; this script uses 15 by default.

set -euo pipefail
cd "$(dirname "$0")"
source ./_env.sh

# ── 1. Create the instance ────────────────────────────────────────────────
if gcloud sql instances describe "${SQL_INSTANCE}" >/dev/null 2>&1; then
  echo "✓ Cloud SQL instance ${SQL_INSTANCE} already exists; skipping create."
else
  echo "▶ Creating Cloud SQL instance ${SQL_INSTANCE} (Postgres 15, ${SQL_TIER}, ${SQL_DISK_GB}GB) — this takes ~5 minutes."
  gcloud sql instances create "${SQL_INSTANCE}" \
    --database-version=POSTGRES_15 \
    --region="${REGION}" \
    --tier="${SQL_TIER}" \
    --storage-size="${SQL_DISK_GB}" \
    --storage-type=SSD \
    --availability-type=zonal \
    --backup-start-time=03:00 \
    --no-deletion-protection
fi

# ── 2. Set the postgres root password (idempotent) ────────────────────────
PG_ROOT_PASSWORD_FILE="/tmp/${SERVICE_NAME}-pg-root.pwd"
if [[ ! -f "${PG_ROOT_PASSWORD_FILE}" ]]; then
  umask 077
  openssl rand -base64 32 > "${PG_ROOT_PASSWORD_FILE}"
fi
chmod 600 "${PG_ROOT_PASSWORD_FILE}"
PG_ROOT_PASSWORD="$(cat "${PG_ROOT_PASSWORD_FILE}")"
gcloud sql users set-password postgres \
  --instance="${SQL_INSTANCE}" \
  --password="${PG_ROOT_PASSWORD}" >/dev/null
echo "ℹ Postgres root password saved at ${PG_ROOT_PASSWORD_FILE} (keep or delete after testing)."

# ── 3. Create the application database ────────────────────────────────────
if gcloud sql databases describe "${SQL_DB_NAME}" --instance="${SQL_INSTANCE}" >/dev/null 2>&1; then
  echo "✓ Database ${SQL_DB_NAME} already exists; skipping create."
else
  echo "▶ Creating database ${SQL_DB_NAME}..."
  gcloud sql databases create "${SQL_DB_NAME}" --instance="${SQL_INSTANCE}"
fi

# ── 4. Create the application user ────────────────────────────────────────
if [[ -z "${SQL_DB_PASSWORD}" ]]; then
  SQL_DB_PASSWORD="$(openssl rand -base64 32 | tr -d '=+/')"
  echo "ℹ Generated SQL_DB_PASSWORD and embedded it in the DATABASE_URL file below."
fi

if gcloud sql users describe "${SQL_DB_USER}" --instance="${SQL_INSTANCE}" >/dev/null 2>&1; then
  echo "▶ Updating ${SQL_DB_USER} password..."
  gcloud sql users set-password "${SQL_DB_USER}" \
    --instance="${SQL_INSTANCE}" \
    --password="${SQL_DB_PASSWORD}" >/dev/null
else
  echo "▶ Creating user ${SQL_DB_USER}..."
  gcloud sql users create "${SQL_DB_USER}" \
    --instance="${SQL_INSTANCE}" \
    --password="${SQL_DB_PASSWORD}" >/dev/null
fi

# ── 5. Print the DATABASE_URL the app will use ────────────────────────────
DB_URL="postgresql+asyncpg://${SQL_DB_USER}:${SQL_DB_PASSWORD}@/${SQL_DB_NAME}?host=/cloudsql/${SQL_CONNECTION_NAME}"
echo
echo "✅ Cloud SQL ready."
echo "   Instance:        ${SQL_INSTANCE}"
echo "   Connection name: ${SQL_CONNECTION_NAME}"
echo "   Database:        ${SQL_DB_NAME}"
echo "   User:            ${SQL_DB_USER}"
echo
# Save it to disk so you can copy it into infrastructure-secrets.env.
DB_URL_FILE="/tmp/${SERVICE_NAME}-database-url"
umask 077
printf '%s' "${DB_URL}" > "${DB_URL_FILE}"
chmod 600 "${DB_URL_FILE}"
echo "   DATABASE_URL written to ${DB_URL_FILE} (mode 600)."
echo "   Copy it into infrastructure-secrets.env, then delete the file when done."
