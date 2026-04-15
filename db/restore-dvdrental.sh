#!/usr/bin/env bash
set -euo pipefail

TAR_PATH="/docker-entrypoint-initdb.d/dvdrental.tar"

if [[ ! -f "$TAR_PATH" ]]; then
  echo "dvdrental restore skipped: $TAR_PATH not found."
  echo "Place the extracted dvdrental.tar at ./db/dvdrental.tar on the host."
  exit 0
fi

echo "Creating database dvdrental (if missing)..."
if psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER}" --dbname postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname = 'dvdrental'" | grep -q 1; then
  echo "Database dvdrental already exists."
else
  createdb --username "${POSTGRES_USER}" dvdrental
fi

echo "Restoring dvdrental from ${TAR_PATH}..."
pg_restore \
  --username "${POSTGRES_USER}" \
  --dbname dvdrental \
  "${TAR_PATH}"

echo "dvdrental restore complete."
