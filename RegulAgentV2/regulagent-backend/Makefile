COMPOSE=docker compose -f docker/compose.dev.yml

.PHONY: up down logs build web db redis migrate makemigrations shell collectstatic restart test test-collect test-verbose test-coverage

up:
	$(COMPOSE) up -d --build db redis web

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

build:
	$(COMPOSE) build web

web:
	$(COMPOSE) up -d web

db:
	$(COMPOSE) up -d db

redis:
	$(COMPOSE) up -d redis

migrate:
	$(COMPOSE) exec web python manage.py migrate

makemigrations:
	$(COMPOSE) exec web python manage.py makemigrations

collectstatic:
	$(COMPOSE) exec web python manage.py collectstatic --noinput

shell:
	$(COMPOSE) exec web python manage.py shell

restart:
	$(COMPOSE) restart web

# Testing targets
test:
	$(COMPOSE) exec -e DJANGO_SETTINGS_MODULE=ra_config.settings.test web python -m pytest

test-collect:
	$(COMPOSE) exec -e DJANGO_SETTINGS_MODULE=ra_config.settings.test web python -m pytest --collect-only

test-verbose:
	$(COMPOSE) exec -e DJANGO_SETTINGS_MODULE=ra_config.settings.test web python -m pytest -vv

test-coverage:
	$(COMPOSE) exec -e DJANGO_SETTINGS_MODULE=ra_config.settings.test web python -m pytest --cov=apps --cov-report=html --cov-report=term


