#!/bin/sh
set -e

echo "[entrypoint] Waiting for database ${DB_HOST}:${DB_PORT:-5432}..."
until PGPASSWORD=$DB_PASSWORD psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c '\q' >/dev/null 2>&1; do
  >&2 echo "[entrypoint] Postgres is unavailable - sleeping"
  sleep 1
done

echo "[entrypoint] Applying migrations..."
python manage.py migrate --noinput

echo "[entrypoint] Collecting static files..."
python manage.py collectstatic --noinput

echo "[entrypoint] Creating cache table if missing..."
python manage.py createcachetable || true

echo "[entrypoint] Starting app: $@"
exec "$@"


