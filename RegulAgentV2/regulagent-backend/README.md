# regulagent-backend

Backend skeleton for RegulAgent (Django + DRF) with Docker-first development. This serves as a golden image to bootstrap new services.

## Prerequisites
- Docker Desktop (Apple Silicon supported)
- Make (optional, for convenience)

## Quick start (development)
1. Copy env
```
cp .env-example .env
```
2. Start services (db, redis, web)
```
docker compose -f docker/compose.dev.yml up -d --build db redis web
```
3. App URL
- Web: http://localhost:8001
- Postgres: host localhost, port 5433
- Redis: host localhost, port 6380

## Project layout
```
regulagent-backend/
├── docker/
│   ├── compose.dev.yml
│   ├── compose.prod.yml
│   ├── db-init/00_extensions.sql
│   ├── Dockerfile
│   └── entrypoint.sh
├── ra_config/
│   ├── asgi.py
│   ├── urls.py
│   ├── wsgi.py
│   └── settings/
│       ├── __init__.py
│       ├── base.py
│       ├── development.py
│       └── production.py
├── requirements/
│   ├── base.txt
│   ├── development.txt
│   └── production.txt
└── manage.py
```

## Settings
- Default: `DJANGO_SETTINGS_MODULE=ra_config.settings.development`
- Production: set `DJANGO_SETTINGS_MODULE=ra_config.settings.production`

Environment variables:
- Database: `DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT`
- Redis: `REDIS_URL`
- OCR providers: `OCR_PROVIDER` (auto|textract|gdocai)
- AWS: `AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_STORAGE_BUCKET_NAME, AWS_S3_REGION_NAME`
- Google Document AI: `GOOGLE_APPLICATION_CREDENTIALS`, `DOC_AI_*`

## Notes on Apple Silicon
- Postgres runs on host port 5433 to avoid conflicts
- Redis runs on host port 6380 to avoid conflicts
- Playwright installs arm64 Chromium in the web image

## Production guidance (AWS)
- API/Workers: ECS Fargate
- DB: RDS Postgres with PostGIS + pgvector
- Cache/Broker: ElastiCache Redis (evaluate SQS broker later)
- Storage: S3 (lifecycle to Glacier)
- Secrets: AWS Secrets Manager + KMS
- Observability: CloudWatch + OpenTelemetry collectors

## Common tasks
- Migrations:
```
docker compose -f docker/compose.dev.yml exec web python manage.py makemigrations
docker compose -f docker/compose.dev.yml exec web python manage.py migrate
```
- Django shell:
```
docker compose -f docker/compose.dev.yml exec web python manage.py shell
```

## License
Proprietary


