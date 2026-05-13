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
from fetch_orders import build_client

app = FastAPI(title="Oda-analyse")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_LINES: pd.DataFrame | None = None
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
    global _LINES, _CART, _BASELINE_IDS
    _LINES = None
    _CART = None
    _BASELINE_IDS = None
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
