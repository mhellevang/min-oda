"""FastAPI + HTMX-app for Oda-analyse.

Kjør:  uv run uvicorn web.main:app --reload --port 8000
       eller: make web

Hver side har egen URL (/handleliste, /diff, /restock). Tabellene oppdateres
in-place via HTMX når slidere eller søk endres — ingen full page reload.
"""

from __future__ import annotations

import sys
from pathlib import Path
from time import time

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from build_list import add_products, create_list, curate, load
from cart_diff import compute_diff, fetch_cart
from fetch_orders import build_client
from restock import compute_cadence

app = FastAPI(title="Oda-analyse")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_LINES = None
_CART = None
_CART_TIME = 0.0
_CART_TTL = 120.0


def get_lines():
    global _LINES
    if _LINES is None:
        _LINES = load()
    return _LINES


def get_cart():
    global _CART, _CART_TIME
    if _CART is None or (time() - _CART_TIME) > _CART_TTL:
        _CART = fetch_cart(build_client())
        _CART_TIME = time()
    return _CART


@app.get("/", response_class=RedirectResponse)
def root():
    return RedirectResponse("/handleliste")


@app.get("/reload", response_class=RedirectResponse)
def reload_data():
    global _LINES, _CART
    _LINES = None
    _CART = None
    return RedirectResponse("/handleliste")


def _build_rows(lines, cycle, top, max_per_cat, search):
    ideal = curate(
        lines, list_cycle_days=cycle, top_n=top, max_per_category=max_per_cat
    )
    if search:
        s = search.lower()
        mask = (
            ideal["key"].astype(str).str.lower().str.contains(s, na=False)
            | ideal["product_name"].astype(str).str.lower().str.contains(s, na=False)
            | ideal["category"].astype(str).str.lower().str.contains(s, na=False)
        )
        ideal = ideal[mask].reset_index(drop=True)

    rows = []
    for _, r in ideal.iterrows():
        rows.append(
            {
                "product_id": int(r["product_id"]),
                "category": r["category"],
                "key": str(r["key"]).capitalize(),
                "product_name": r["product_name"],
                "qty": int(r["foreslått_antall"]),
                "median": int(round(r["median_days"])),
                "last": r["last"].date().isoformat()
                if r["last"] is not None
                else "—",
            }
        )
    return rows


@app.get("/handleliste", response_class=HTMLResponse)
def handleliste(
    request: Request,
    cycle: int = 14,
    top: int = 40,
    max_per_cat: int = 8,
    search: str = "",
):
    rows = _build_rows(get_lines(), cycle, top, max_per_cat, search)
    return templates.TemplateResponse(
        request,
        "handleliste.html",
        {
            "active": "handleliste",
            "cycle": cycle,
            "top": top,
            "max_per_cat": max_per_cat,
            "search": search,
            "rows": rows,
        },
    )


@app.get("/handleliste/table", response_class=HTMLResponse)
def handleliste_table(
    request: Request,
    cycle: int = 14,
    top: int = 40,
    max_per_cat: int = 8,
    search: str = "",
):
    rows = _build_rows(get_lines(), cycle, top, max_per_cat, search)
    return templates.TemplateResponse(
        request, "_list_table.html", {"rows": rows}
    )


@app.post("/handleliste/create", response_class=HTMLResponse)
async def handleliste_create(request: Request):
    form = await request.form()
    title = form.get("title") or "Ukehandel — familien"
    cycle = int(form.get("cycle") or 14)

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
    result = create_list(client, title, f"Faste varer · {cycle} d syklus")
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


def _build_diff_rows(lines, cart, cycle, top, max_per_cat, top_up, search):
    ideal = curate(
        lines, list_cycle_days=cycle, top_n=top, max_per_category=max_per_cat
    )
    missing = compute_diff(ideal, cart, top_up=top_up)
    if search:
        s = search.lower()
        mask = (
            missing["key"].astype(str).str.lower().str.contains(s, na=False)
            | missing["product_name"].astype(str).str.lower().str.contains(s, na=False)
            | missing["category"].astype(str).str.lower().str.contains(s, na=False)
        )
        missing = missing[mask].reset_index(drop=True)

    rows = []
    for _, r in missing.iterrows():
        rows.append(
            {
                "product_id": int(r["product_id"]),
                "category": r["category"],
                "key": str(r["key"]).capitalize(),
                "product_name": r["product_name"],
                "forslag": int(r["foreslått_antall"]),
                "i_kurv": int(r["i_kurv"]),
                "mangler": int(r["mangler"]),
                "median": int(round(r["median_days"])),
            }
        )
    return rows


@app.get("/diff", response_class=HTMLResponse)
def diff(
    request: Request,
    cycle: int = 14,
    top: int = 40,
    max_per_cat: int = 8,
    top_up: bool = False,
    search: str = "",
):
    cart = get_cart()
    cart_total = int(cart["quantity"].sum()) if not cart.empty else 0
    rows = _build_diff_rows(
        get_lines(), cart, cycle, top, max_per_cat, top_up, search
    )
    return templates.TemplateResponse(
        request,
        "diff.html",
        {
            "active": "diff",
            "cycle": cycle,
            "top": top,
            "max_per_cat": max_per_cat,
            "top_up": top_up,
            "search": search,
            "rows": rows,
            "cart_total": cart_total,
        },
    )


@app.get("/diff/table", response_class=HTMLResponse)
def diff_table(
    request: Request,
    cycle: int = 14,
    top: int = 40,
    max_per_cat: int = 8,
    top_up: bool = False,
    search: str = "",
):
    rows = _build_diff_rows(
        get_lines(), get_cart(), cycle, top, max_per_cat, top_up, search
    )
    return templates.TemplateResponse(
        request, "_diff_table.html", {"rows": rows}
    )


@app.post("/diff/create", response_class=HTMLResponse)
async def diff_create(request: Request):
    form = await request.form()
    title = form.get("title") or "Resterende — ukehandel"

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
    result = create_list(
        client, title, "Diff mellom faste varer og handlekurv"
    )
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


_STATUS_CLASS = {
    "forfalt": "forfalt",
    "akkurat nå": "nå",
    "snart": "snart",
    "i rute": "rute",
}


def _format_due(d: int) -> str:
    if d == 0:
        return "i dag"
    if d < 0:
        return f"{-int(d)} d siden"
    return f"om {int(d)} d"


def _build_restock_rows(lines, horizon, show_all, by_type, search):
    cadence = compute_cadence(lines, by_type=by_type)
    if cadence.empty:
        return [], {"forfalt": 0, "nå": 0, "snart": 0, "total": 0}

    summary = {
        "forfalt": int((cadence["status"] == "forfalt").sum()),
        "nå": int((cadence["status"] == "akkurat nå").sum()),
        "snart": int((cadence["status"] == "snart").sum()),
        "total": len(cadence),
    }

    view_df = cadence if show_all else cadence[
        cadence["days_until_due"] <= horizon
    ]
    if search:
        s = search.lower()
        mask = (
            view_df["key"].astype(str).str.lower().str.contains(s, na=False)
            | view_df["product_name"].astype(str).str.lower().str.contains(s, na=False)
            | view_df["category"].astype(str).str.lower().str.contains(s, na=False)
        )
        view_df = view_df[mask]

    rows = []
    for _, r in view_df.iterrows():
        rows.append(
            {
                "key": str(r["key"]).capitalize(),
                "product_name": r["product_name"],
                "category": r["category"],
                "n_buys": int(r["n_buys"]),
                "last": r["last"].date().isoformat() if r["last"] is not None else "—",
                "median": int(round(r["median_days"])),
                "due_text": _format_due(int(r["days_until_due"])),
                "status": r["status"],
                "status_class": _STATUS_CLASS.get(r["status"], "rute"),
            }
        )
    return rows, summary


@app.get("/restock", response_class=HTMLResponse)
def restock(
    request: Request,
    horizon: int = 14,
    show_all: bool = False,
    by_type: bool = True,
    search: str = "",
):
    rows, summary = _build_restock_rows(
        get_lines(), horizon, show_all, by_type, search
    )
    return templates.TemplateResponse(
        request,
        "restock.html",
        {
            "active": "restock",
            "horizon": horizon,
            "show_all": show_all,
            "by_type": by_type,
            "search": search,
            "rows": rows,
            "summary": summary,
        },
    )


@app.get("/restock/table", response_class=HTMLResponse)
def restock_table(
    request: Request,
    horizon: int = 14,
    show_all: bool = False,
    by_type: bool = True,
    search: str = "",
):
    rows, summary = _build_restock_rows(
        get_lines(), horizon, show_all, by_type, search
    )
    return templates.TemplateResponse(
        request,
        "_restock_table.html",
        {"rows": rows, "summary": summary, "horizon": horizon},
    )
