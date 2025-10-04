COMPOSE=docker compose -f docker/compose.dev.yml

.PHONY: up down logs build web db redis migrate makemigrations shell collectstatic restart

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


