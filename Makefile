PY ?= uv run python

.PHONY: help refresh web basket portrait seasonality restock all clean

help:
	@echo "Targets:"
	@echo "  refresh  - hent nye ordrer fra Oda"
	@echo "  web      - start FastAPI + HTMX-app på :8000"
	@echo "  all      - refresh (web starter manuelt)"
	@echo "  clean    - slett genererte plots"
	@echo ""
	@echo "  basket / portrait / seasonality / restock  - enkeltanalyser (CLI)"

refresh:
	$(PY) fetch_orders.py

basket:
	$(PY) basket.py

portrait:
	$(PY) portrait.py

seasonality:
	$(PY) seasonality.py

restock:
	$(PY) restock.py

web:
	uv run uvicorn web.main:app --reload --port 8000

all: refresh

clean:
	rm -rf plots/*.png
