PY ?= uv run python

.PHONY: help refresh web all

help:
	@echo "Targets:"
	@echo "  refresh  - hent nye ordrer fra Oda + bygg CSV-er"
	@echo "  web      - start FastAPI + HTMX-app på :8000"
	@echo "  all      - refresh + web"

refresh:
	$(PY) fetch_orders.py

web:
	uv run uvicorn web.main:app --reload --port 8000

all: refresh web
