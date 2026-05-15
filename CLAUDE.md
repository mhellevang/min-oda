# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All Python is invoked via `uv` (Python pinned to 3.12 in `.python-version` — `rookiepy` lacks wheels for 3.13+).

```sh
uv sync                                 # install deps
uv run min-oda                          # start FastAPI app on http://localhost:8000
uv run pytest                           # run the test suite
uv run pytest tests/test_restock.py     # single test file
uv run pytest -k cadence                # filter by name
uv run python -m min_oda.fetch_orders   # force a fresh fetch from oda.com
uv run python -m min_oda.fetch_orders --url '<URL>'   # override endpoint if Oda changes it
```

Web tests in `tests/test_web.py` skip when `data/orders.csv` is missing — that is expected in a fresh checkout.

## Language and writing style

Code, comments, commit messages, and UI strings are in Norwegian (bokmål). Match the existing register: dry and matter-of-fact, no marketing words like "smart" or "personlig assistent", no em-dashes (use commas or parentheses), and no "vi" in user-facing copy since this is a single-user app.

## Architecture

Two layers: a data pipeline that pulls order history from oda.com into CSVs, and a FastAPI web app that reads those CSVs to drive shopping lists + insights.

### Data pipeline (`min_oda/fetch_orders.py`)

1. `build_client()` in `oda_client.py` assembles an `httpx.Client` with Oda cookies. Source order:
   - `ODA_COOKIE` / `ODA_SESSIONID` from `.env` if present.
   - Otherwise `auth.load_browser_cookies()` reads them from an installed browser via `rookiepy`. Platform-specific order (Firefox first on macOS to avoid the Keychain prompt). `ODA_BROWSER` env var overrides.
   - `last_auth_source()` + `auth_error_hint()` give human-readable feedback when credentials expire.
2. Orders are paged through `GET /api/v1/orders/`, then per-order details are fetched to `data/order_details/*.json`.
3. `build_csvs()` flattens the JSON to `data/orders.csv` (one row per order) and `data/lines.csv` (one row per line item).
4. `maybe_refresh_data()` is the orchestration entrypoint called both at app startup (lifespan) and from `POST /refresh`. It checks the age of `data/orders.json` and refetches if older than 24 h (or `force=True`).

### Analysis layer (pure pandas)

All analysis modules accept DataFrames from `data_loader.load_both()` and return DataFrames or dicts — no I/O, so they're easy to unit-test.

- `product_types.py` — classifies a product into a varetype ("brød", "melk", …). Lookup order: explicit `data/product_types.json` mapping → keyword regex on the product name → coarse category mapping. Used everywhere we want substitutable brands to count as the same need.
- `restock.py:compute_cadence(lines, by_type=True)` — for each product (or varetype), computes median interval between purchases, `days_since`, `days_until_due`, status (forfalt/snart/i rute), and CV. Drops abandoned products (`days_since > median * abandon_factor`) and rare ones (`median > 90 d`). The `by_type=True` path is the foundation for both shopping-list features.
- `build_list.py:curate(lines, list_cycle_days, top_n, max_per_category, blocked)` — picks the *most-bought* product as the representative for each cadence-stable varetype, computes `foreslått_antall = ceil(cycle / median)`, applies category priority + caps. The blocklist filters out specific `product_id`s while leaving the varetype available (another variant can step in).
- `cart_diff.py:compute_diff(ideal, cart, top_up)` — joins curated list against the live cart *by varetype, not product_id*. Default returns only varetypes missing from the cart. `top_up=True` also includes those with too-low quantity.
- `blocklist.py` — JSON-backed persistent block list at `data/blocklist.json`. `block(pid)` / `unblock(pid)` / `blocked_ids()` / `list_blocked()`.

### Web app (`min_oda/web/`)

FastAPI + Jinja2 + HTMX. Two pages:

- `/handleliste` — same `_build_rows()` powers two modes via the `new_list` query param:
  - Default: diff against the live cart, show only missing items (`compute_diff`).
  - `?new_list=true`: ignore cart, show the full curated list.
  Sliders (cycle/top/max_per_cat) and search are query params. `is_extra` flags rows that wouldn't appear at default filters — UI marks these with an accent stripe. `_mode_urls()` preserves filters when switching modes.
- `/innsikt` — KPIs, monthly-spend plot (matplotlib `Agg` → base64 PNG), staples, cuisine/cooking/health profiles, top products + categories, seasonality, and basket-analysis (lift + support pairs, plus product lookup). All compute lives in `web/innsikt.py`.

State is cached in module-level globals (`_ORDERS`, `_LINES`, `_CART`, `_BASKET_CACHE`, `_BASELINE_IDS`). `invalidate_caches()` resets all of them and is called after a successful `POST /refresh`; `invalidate_blocklist_caches()` resets only the baseline ids after a block/unblock. The cart has its own 120 s TTL.

HTMX endpoints (`/handleliste/table`, `/handleliste/block`, `/handleliste/unblock`, `/innsikt/basket-lookup`) return template fragments (files prefixed with `_`).

The CLI entrypoint is `min_oda.web.cli:run` (registered in `pyproject.toml` as `[project.scripts] min-oda`). It just wraps `uvicorn.run(...)`.

### Data files

Everything under `data/` is gitignored except `product_types.json`. Don't commit cookies, CSVs, or order JSON.

## Testing notes

`tests/conftest.py` builds synthetic `lines` DataFrames anchored to a fixed `TODAY = 2026-05-14` so cadence thresholds (abandon, max_median, status) are deterministic. When adding new cadence/curation rules, add a fixture rather than mutating an existing one.

## Talking to Oda

The `POST /api/v1/product-lists/<id>/products/` payload shape is undocumented. `oda_client.add_products()` tries three shapes (`product_id+quantity`, `product+quantity`, `product.id+quantity`) and logs which one works. If you confirm the accepted shape, pin it in `_ADD_PRODUCTS_PAYLOAD_SHAPES` and drop the others.
