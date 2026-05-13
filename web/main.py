"""FastAPI + HTMX-app for Oda-analyse.

Kjør:  uv run uvicorn web.main:app --reload --port 8000
       eller: make web

Én side, `/handleliste`, med to moduser:
  - Default: sammenlign med kurven på Oda og vis kun varer som mangler
    (siden det er det vanligste arbeidsflyten — supplere en eksisterende
    kurv før innlevering).
  - "Lag fersk handleliste": bygg en komplett ukehandel-liste fra bunnen
    av, uavhengig av hva som ligger i kurven.

Tabellen oppdateres in-place via HTMX når slidere eller søk endres — ingen
full page reload. Selve modus-toggelen utløser full page reload så URL,
kolonner og knappetekst speiler valgt modus.
"""

from __future__ import annotations

import sys
from pathlib import Path
from time import time
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from build_list import add_products, create_list, curate, load
from cart_diff import compute_diff, fetch_cart
from data_loader import load_both
from fetch_orders import build_client

from . import innsikt

app = FastAPI(title="Oda-analyse")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_LINES: pd.DataFrame | None = None
_ORDERS: pd.DataFrame | None = None
_LINES_FULL: pd.DataFrame | None = None  # for /innsikt — har 'date' fra orders-merge
_BASKET_CACHE: tuple | None = None
_CART: pd.DataFrame | None = None
_CART_TIME = 0.0
_CART_TTL = 120.0

DEFAULT_CYCLE = 14
DEFAULT_TOP = 40
DEFAULT_MAX_PER_CAT = 8

_BASELINE_IDS: set[int] | None = None

_STATUS_CLASS = {
    "forfalt": "forfalt",
    "akkurat nå": "nå",
    "snart": "snart",
    "i rute": "rute",
}


def get_lines() -> pd.DataFrame:
    global _LINES
    if _LINES is None:
        _LINES = load()
    return _LINES


def get_cart() -> pd.DataFrame:
    global _CART, _CART_TIME
    if _CART is None or (time() - _CART_TIME) > _CART_TTL:
        _CART = fetch_cart(build_client())
        _CART_TIME = time()
    return _CART


def get_orders_and_lines() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load orders + lines (med date joined) — cached. For /innsikt."""
    global _ORDERS, _LINES_FULL
    if _ORDERS is None or _LINES_FULL is None:
        _ORDERS, _LINES_FULL = load_both()
    return _ORDERS, _LINES_FULL


def get_basket() -> tuple:
    """Cache de tunge basket-parene — disse er O(n²) i antall vanlige
    produkter, så bare regn én gang per data-reload."""
    global _BASKET_CACHE
    if _BASKET_CACHE is None:
        _, lines = get_orders_and_lines()
        _BASKET_CACHE = innsikt.basket_pairs(lines)
    return _BASKET_CACHE


def get_baseline_ids() -> set[int]:
    """Produkt-id-er som er med ved default-filtre — brukes til å markere
    rader som *kommer til* når brukeren utvider filtrene."""
    global _BASELINE_IDS
    if _BASELINE_IDS is None:
        baseline = curate(
            get_lines(),
            list_cycle_days=DEFAULT_CYCLE,
            top_n=DEFAULT_TOP,
            max_per_category=DEFAULT_MAX_PER_CAT,
        )
        _BASELINE_IDS = (
            {int(x) for x in baseline["product_id"]}
            if not baseline.empty
            else set()
        )
    return _BASELINE_IDS


def _mode_urls(
    cycle: int, top: int, max_per_cat: int, search: str, top_up: bool
) -> tuple[str, str]:
    """Bygg URL-er for modus-bytte som preserverer ikke-default filtre."""
    base: dict[str, str | int] = {}
    if cycle != 14:
        base["cycle"] = cycle
    if top != 40:
        base["top"] = top
    if max_per_cat != 8:
        base["max_per_cat"] = max_per_cat
    if search:
        base["search"] = search

    diff_params = dict(base)
    if top_up:
        diff_params["top_up"] = "true"
    new_list_params = dict(base, new_list="true")

    url_diff = "/handleliste"
    if diff_params:
        url_diff = f"{url_diff}?{urlencode(diff_params)}"
    url_new_list = f"/handleliste?{urlencode(new_list_params)}"
    return url_diff, url_new_list


def _format_due(d: int) -> str:
    if d == 0:
        return "i dag"
    if d < 0:
        return f"{-int(d)} d siden"
    return f"om {int(d)} d"


def _build_rows(
    lines: pd.DataFrame,
    cycle: int,
    top: int,
    max_per_cat: int,
    search: str,
    new_list: bool,
    top_up: bool,
) -> tuple[list[dict], int, int]:
    ideal = curate(
        lines, list_cycle_days=cycle, top_n=top, max_per_category=max_per_cat
    )
    if ideal.empty:
        return [], 0, 0

    baseline_ids = get_baseline_ids()

    cart_total = 0
    if not new_list:
        cart = get_cart()
        cart_total = int(cart["quantity"].sum()) if not cart.empty else 0
        ideal = compute_diff(ideal, cart, top_up=top_up)

    if search and not ideal.empty:
        s = search.lower()
        mask = (
            ideal["key"].astype(str).str.lower().str.contains(s, na=False)
            | ideal["product_name"].astype(str).str.lower().str.contains(s, na=False)
            | ideal["category"].astype(str).str.lower().str.contains(s, na=False)
        )
        ideal = ideal[mask].reset_index(drop=True)

    rows: list[dict] = []
    extra_count = 0
    for _, r in ideal.iterrows():
        forslag = int(r["foreslått_antall"])
        if new_list:
            i_kurv = None
            mangler = None
            default_qty = forslag
        else:
            i_kurv = int(r["i_kurv"])
            mangler = int(r["mangler"])
            default_qty = mangler

        pid = int(r["product_id"])
        is_extra = pid not in baseline_ids
        if is_extra:
            extra_count += 1

        rows.append(
            {
                "product_id": pid,
                "category": r["category"],
                "key": str(r["key"]).capitalize(),
                "product_name": r["product_name"],
                "forslag": forslag,
                "i_kurv": i_kurv,
                "mangler": mangler,
                "qty": default_qty,
                "median": int(round(r["median_days"])),
                "status": r["status"],
                "status_class": _STATUS_CLASS.get(r["status"], "rute"),
                "due_text": _format_due(int(r["days_until_due"])),
                "last": r["last"].date().isoformat()
                if r["last"] is not None
                else "—",
                "is_extra": is_extra,
            }
        )
    return rows, cart_total, extra_count


@app.get("/", response_class=RedirectResponse)
def root() -> RedirectResponse:
    return RedirectResponse("/handleliste")


# Eldre URL-er fra forrige multi-tab-design — bevares som redirects.
@app.get("/diff", response_class=RedirectResponse)
def legacy_diff() -> RedirectResponse:
    return RedirectResponse("/handleliste")


@app.get("/restock", response_class=RedirectResponse)
def legacy_restock() -> RedirectResponse:
    return RedirectResponse("/handleliste")


@app.get("/reload", response_class=RedirectResponse)
def reload_data() -> RedirectResponse:
    global _LINES, _ORDERS, _LINES_FULL, _CART, _BASELINE_IDS, _BASKET_CACHE
    _LINES = None
    _ORDERS = None
    _LINES_FULL = None
    _CART = None
    _BASELINE_IDS = None
    _BASKET_CACHE = None
    return RedirectResponse("/handleliste")


@app.get("/handleliste", response_class=HTMLResponse)
def handleliste(
    request: Request,
    cycle: int = 14,
    top: int = 40,
    max_per_cat: int = 8,
    search: str = "",
    new_list: bool = False,
    top_up: bool = False,
) -> HTMLResponse:
    rows, cart_total, extra_count = _build_rows(
        get_lines(), cycle, top, max_per_cat, search, new_list, top_up
    )
    url_diff, url_new_list = _mode_urls(cycle, top, max_per_cat, search, top_up)
    return templates.TemplateResponse(
        request,
        "handleliste.html",
        {
            "active": "handleliste",
            "cycle": cycle,
            "top": top,
            "max_per_cat": max_per_cat,
            "search": search,
            "new_list": new_list,
            "top_up": top_up,
            "rows": rows,
            "cart_total": cart_total,
            "extra_count": extra_count,
            "url_diff": url_diff,
            "url_new_list": url_new_list,
        },
    )


@app.get("/handleliste/table", response_class=HTMLResponse)
def handleliste_table(
    request: Request,
    cycle: int = 14,
    top: int = 40,
    max_per_cat: int = 8,
    search: str = "",
    new_list: bool = False,
    top_up: bool = False,
) -> HTMLResponse:
    rows, _, extra_count = _build_rows(
        get_lines(), cycle, top, max_per_cat, search, new_list, top_up
    )
    return templates.TemplateResponse(
        request, "_list_table.html",
        {"rows": rows, "new_list": new_list, "extra_count": extra_count},
    )


@app.post("/handleliste/create", response_class=HTMLResponse)
async def handleliste_create(request: Request) -> HTMLResponse:
    form = await request.form()
    new_list = form.get("new_list") == "true"
    cycle = int(form.get("cycle") or 14)
    default_title = (
        "Ukehandel — familien" if new_list else "Resterende — ukehandel"
    )
    title = form.get("title") or default_title

    items: list[tuple[int, int]] = []
    for k, v in form.items():
        if not k.startswith("qty_"):
            continue
        try:
            qty = int(v)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        try:
            pid = int(k.removeprefix("qty_"))
        except ValueError:
            continue
        items.append((pid, qty))

    if not items:
        return HTMLResponse(
            '<div class="alert warn">Ingen varer å legge til (alle på 0).</div>'
        )

    client = build_client()
    desc = (
        f"Faste varer · {cycle} d syklus"
        if new_list
        else "Diff mellom faste varer og handlekurv"
    )
    result = create_list(client, title, desc)
    if not result:
        return HTMLResponse(
            '<div class="alert error">Kunne ikke opprette listen.</div>'
        )
    list_id = result["id"]
    ok = add_products(client, list_id, items)
    return HTMLResponse(
        f'<div class="alert ok">La til {ok}/{len(items)} varer. '
        f'<a href="https://oda.com/no/account/lists/details/{list_id}/" '
        f'target="_blank">Åpne på Oda →</a></div>'
    )


# ---------- /innsikt -----------------------------------------------------


@app.get("/innsikt", response_class=HTMLResponse)
def innsikt_page(request: Request, q: str = "") -> HTMLResponse:
    orders, lines = get_orders_and_lines()
    pairs, counts, name_map, n_orders = get_basket()
    basket_lookup = (
        innsikt.basket_for_product(pairs, name_map, counts, n_orders, q)
        if q
        else None
    )
    return templates.TemplateResponse(
        request,
        "innsikt.html",
        {
            "active": "innsikt",
            "kpis": innsikt.kpis(orders, lines),
            "monthly_plot": innsikt.monthly_spend_plot_b64(orders),
            "staples": innsikt.staples(orders, lines),
            "cuisines": innsikt.cuisine_mix(lines),
            "baby": innsikt.baby_signal(lines),
            "cooking": innsikt.cooking_style(lines),
            "prices": innsikt.price_consciousness(lines),
            "health": innsikt.health(lines),
            "beverages": innsikt.beverages(lines),
            "top_p": innsikt.top_products(lines),
            "top_c": innsikt.top_categories(lines),
            "seasonal": innsikt.seasonal_products(lines),
            "july": innsikt.july_gap(orders),
            "basket_lift": innsikt.top_lift_pairs(pairs, name_map),
            "basket_support": innsikt.top_support_pairs(pairs, name_map),
            "basket_q": q,
            "basket_lookup": basket_lookup,
        },
    )


@app.get("/innsikt/basket-lookup", response_class=HTMLResponse)
def innsikt_basket_lookup(request: Request, q: str = "") -> HTMLResponse:
    pairs, counts, name_map, n_orders = get_basket()
    basket_lookup = (
        innsikt.basket_for_product(pairs, name_map, counts, n_orders, q)
        if q
        else None
    )
    return templates.TemplateResponse(
        request, "_basket_lookup.html", {"basket_lookup": basket_lookup}
    )
