PY ?= uv run python

.PHONY: help refresh report tables gui web basket portrait seasonality restock prices all clean

help:
	@echo "Targets:"
	@echo "  refresh  - hent nye ordrer fra Oda"
	@echo "  report   - generer HTML-rapport (report.py)"
	@echo "  tables   - kjør alle terminal-analyser"
	@echo "  gui      - start Streamlit-app i nettleseren"
	@echo "  web      - start FastAPI + HTMX-app (POC, kun handleliste)"
	@echo "  all      - refresh + report"
	@echo "  clean    - slett genererte plots"
	@echo ""
	@echo "  basket / portrait / seasonality / restock / prices  - enkeltanalyser"

refresh:
	$(PY) fetch_orders.py

report:
	$(PY) report.py

basket:
	$(PY) basket.py

portrait:
	$(PY) portrait.py

seasonality:
	$(PY) seasonality.py

restock:
	$(PY) restock.py

prices:
	$(PY) prices.py

tables: portrait seasonality basket restock prices

gui:
	uv run streamlit run app.py

web:
	uv run uvicorn web.main:app --reload --port 8000

all: refresh report

clean:
	rm -rf plots/*.png
