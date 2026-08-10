"""FastAPI + HTMX-app for Oda-analyse.

Kjør:  uv run min-oda

To faner:
  /handleliste — bygg handlelister eller suppler kurven din på Oda.
  /innsikt     — mønstre i hva du faktisk handler.

Data hentes automatisk ved oppstart hvis orders.json er over 24 t gammel,
og kan også oppdateres on-demand via knappen i navigasjonen (POST /refresh).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from time import time
from urllib.parse import quote, urlencode

import httpx
import pandas as pd
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import math

from . import auth
from .. import blocklist, engangsvarer, representatives
from ..build_list import curate
from ..cart_diff import compute_diff, fetch_cart
from ..data_loader import load_both
from ..fetch_orders import maybe_refresh_data
from ..images import latest_product_images
from ..prices import latest_unit_prices
from ..oda_client import (
    MissingCredentials,
    add_products,
    add_to_cart,
    build_client,
    create_list,
    search_products,
)
from ..restock import compute_cadence
from ..variants import variants_for_type

from . import innsikt

log = logging.getLogger("min-oda")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Sjekker om data er ferskt nok og henter ev. nytt fra Oda før appen
    begynner å svare på requests. Feil sluker vi — appen starter likevel
    med eksisterende data, og UI-banneret forklarer hva som er galt."""
    global _REFRESH_STATUS
    _REFRESH_STATUS = maybe_refresh_data()
    if _REFRESH_STATUS.get("refreshed"):
        log.info("Data oppdatert ved oppstart.")
    elif _REFRESH_STATUS.get("error"):
        log.warning("Refresh ved oppstart feilet: %s", _REFRESH_STATUS["error"])
    yield


app = FastAPI(title="Min Oda", lifespan=lifespan)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Alt bak et app-passord (APP_PASSWORD). /login, /logout og statiske filer er
# alltid åpne; er passordet tomt er auth av (lokalt / bak VPN).
_OPEN_PREFIXES = ("/login", "/logout", "/static")


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    is_open = any(path == p or path.startswith(p + "/") for p in _OPEN_PREFIXES)
    if not is_open and not auth.is_authed(request):
        target = f"/login?next={quote(path)}"
        # HTMX-fragmenter: la htmx gjøre en full redirect i stedet for å
        # bytte inn login-siden i et fragment.
        if request.headers.get("HX-Request") == "true":
            resp = Response(status_code=401)
            resp.headers["HX-Redirect"] = target
            return resp
        return RedirectResponse(url=target, status_code=303)
    return await call_next(request)


def _safe_next(next_url: str) -> str:
    """Kun relative samme-side-stier, ellers blir /login?next=… en åpen
    redirect (phishing)."""
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/handleliste"


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/handleliste", error: int = 0):
    next = _safe_next(next)
    if not auth.auth_enabled() or auth.is_authed(request):
        return RedirectResponse(url=next, status_code=303)
    return templates.TemplateResponse(
        request, "login.html", {"next": next, "error": bool(error)}
    )


@app.post("/login")
def login_submit(password: str = Form(...), next: str = Form("/handleliste")):
    next = _safe_next(next)
    if auth.check_password(password):
        resp = RedirectResponse(url=next, status_code=303)
        resp.set_cookie(
            auth.COOKIE_NAME,
            auth.make_token(),
            httponly=True,
            samesite="lax",
            secure=auth.cookie_secure(),
            max_age=60 * 60 * 24 * 30,
        )
        return resp
    return RedirectResponse(url=f"/login?error=1&next={quote(next)}", status_code=303)


@app.post("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie(auth.COOKIE_NAME)
    return resp

# htmx serveres lokalt (ikke fra CDN) så appen funker offline.
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).parent / "static")),
    name="static",
)


def _register_template_globals() -> None:
    """Eksponer refresh_status() og format-filtre til alle templates uten
    å måtte legge det inn manuelt i hver TemplateResponse."""
    templates.env.globals["refresh_status"] = refresh_status_ctx
    templates.env.globals["auth_enabled"] = auth.auth_enabled
    templates.env.filters["format_age"] = format_age
    templates.env.filters["format_days_ago"] = format_days_ago
    templates.env.filters["format_kr"] = format_kr
    templates.env.filters["format_int"] = format_int
    templates.env.filters["format_dato"] = _no_short_date


# Registrert i bunnen av modulen, etter at refresh_status_ctx er definert.

_ORDERS: pd.DataFrame | None = None
_LINES: pd.DataFrame | None = None  # date er joined fra orders — brukes av både /handleliste og /innsikt
_BASKET_CACHE: tuple | None = None
_CART: pd.DataFrame | None = None
_CART_TIME = 0.0
_CART_TTL = 120.0

DEFAULT_CYCLE = 7
DEFAULT_TOP = 40
DEFAULT_MAX_PER_CAT = 8

_BASELINE_IDS: set[int] | None = None
_CADENCE_BY_TYPE: pd.DataFrame | None = None
_PRICE_MAP: dict[int, float] | None = None
_IMAGE_MAP: dict[int, str] | None = None
_VARIANT_LIMIT = 10

_REFRESH_STATUS: dict = {
    "refreshed": False,
    "data_age_hours": None,
    "error": None,
}

_NB_MONTHS_SHORT = ["", "jan", "feb", "mar", "apr", "mai", "jun",
                    "jul", "aug", "sep", "okt", "nov", "des"]


def _no_short_date(ts) -> str:
    """Norsk kort datoform: '14. mai' i år, '14. mai 2025' ellers."""
    if ts is None or pd.isna(ts):
        return "—"
    d = pd.Timestamp(ts)
    today = pd.Timestamp.now()
    if d.year == today.year:
        return f"{d.day}. {_NB_MONTHS_SHORT[d.month]}"
    return f"{d.day}. {_NB_MONTHS_SHORT[d.month]} {d.year}"


def format_age(hours: float | None) -> str:
    """Jinja-filter: alder i timer → 'X min siden' / 'X t siden' / …"""
    if hours is None:
        return "ukjent"
    if hours < 1:
        mins = max(1, int(hours * 60))
        return f"{mins} min siden"
    if hours < 24:
        return f"{int(hours)} t siden"
    days = int(hours / 24)
    if days < 30:
        return f"{days} d siden"
    return f"{days // 30} mnd siden"


def format_kr(value) -> str:
    """Jinja-filter: kroner → '1 234 kr' (heltall, mellomrom som tusenskille).
    Returnerer '–' for manglende pris. Avrunding matcher JS-en som regner
    totalsummen live."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "–"
    return f"{round(float(value)):,d} kr".replace(",", " ")


def format_int(value) -> str:
    """Jinja-filter: tall → '1 234' (mellomrom som tusenskille), '–' for
    manglende verdi. Som format_kr, uten kr-suffiks."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "–"
    return f"{round(float(value)):,d}".replace(",", " ")


def format_days_ago(days: int | float | None) -> str:
    """Jinja-filter: dager siden et tidspunkt → menneskelig norsk."""
    if days is None:
        return "—"
    days = int(days)
    if days < 0:
        return f"om {-days} d"
    if days == 0:
        return "i dag"
    if days == 1:
        return "i går"
    if days < 30:
        return f"{days} d siden"
    if days < 365:
        return f"{days // 30} mnd siden"
    return f"{days // 365} år siden"


def refresh_status_ctx() -> dict:
    return {
        "data_age_hours": _REFRESH_STATUS.get("data_age_hours"),
        "error": _REFRESH_STATUS.get("error"),
    }


def invalidate_caches() -> None:
    """Nullstill alle in-memory caches — kalles etter en vellykket refresh."""
    global _LINES, _ORDERS, _CART, _BASELINE_IDS, _BASKET_CACHE, _CADENCE_BY_TYPE
    global _PRICE_MAP, _IMAGE_MAP
    _LINES = None
    _ORDERS = None
    _CART = None
    _BASELINE_IDS = None
    _BASKET_CACHE = None
    _CADENCE_BY_TYPE = None
    _PRICE_MAP = None
    _IMAGE_MAP = None


def invalidate_cart_cache() -> None:
    """Tving en frisk fetch_cart() ved neste lesing — kalles etter at vi
    har lagt noe i kurven."""
    global _CART, _CART_TIME
    _CART = None
    _CART_TIME = 0.0


def _load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    global _ORDERS, _LINES
    if _ORDERS is None or _LINES is None:
        _ORDERS, _LINES = load_both()
    return _ORDERS, _LINES


def get_lines() -> pd.DataFrame:
    return _load_data()[1]


_EMPTY_CART = pd.DataFrame(
    columns=["product_id", "product_name", "category", "quantity", "_type"]
)


def get_cart() -> pd.DataFrame:
    """Hent handlekurv fra Oda. Hvis cookies mangler, returner tom kurv så
    siden fortsatt rendrer. Auth-banneret over forteller brukeren hva som
    er galt."""
    global _CART, _CART_TIME
    if _CART is None or (time() - _CART_TIME) > _CART_TTL:
        try:
            _CART = fetch_cart(build_client())
        except MissingCredentials:
            _CART = _EMPTY_CART
        _CART_TIME = time()
    return _CART


def get_orders_and_lines() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load orders + lines (med date joined) — cached. For /innsikt."""
    return _load_data()


def get_basket() -> tuple:
    """Cache de tunge basket-parene — disse er O(n²) i antall vanlige
    produkter, så bare regn én gang per data-reload."""
    global _BASKET_CACHE
    if _BASKET_CACHE is None:
        _, lines = get_orders_and_lines()
        _BASKET_CACHE = innsikt.basket_pairs(lines)
    return _BASKET_CACHE


def get_cadence_by_type() -> pd.DataFrame:
    """Cache compute_cadence(by_type=True) — beregnes én gang per
    data-reload og brukes av både rad-bygger og variant-endepunktene."""
    global _CADENCE_BY_TYPE
    if _CADENCE_BY_TYPE is None:
        _CADENCE_BY_TYPE = compute_cadence(get_lines(), by_type=True)
    return _CADENCE_BY_TYPE


def get_price_map() -> dict[int, float]:
    """Cache sist-betalt-pris per produkt — beregnes én gang per data-reload."""
    global _PRICE_MAP
    if _PRICE_MAP is None:
        _PRICE_MAP = latest_unit_prices(get_lines())
    return _PRICE_MAP


def get_image_map() -> dict[int, str]:
    """Cache bilde-URL per produkt — beregnes én gang per data-reload."""
    global _IMAGE_MAP
    if _IMAGE_MAP is None:
        _IMAGE_MAP = latest_product_images(get_lines())
    return _IMAGE_MAP


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
            blocked=blocklist.blocked_ids(),
            blocked_types=blocklist.blocked_types(),
            chosen=representatives.chosen_representatives(),
        )
        _BASELINE_IDS = (
            {int(x) for x in baseline["product_id"]}
            if not baseline.empty
            else set()
        )
    return _BASELINE_IDS


def invalidate_blocklist_caches() -> None:
    """Etter en blokk/avblokk er baseline ikke lenger riktig."""
    global _BASELINE_IDS
    _BASELINE_IDS = None


def invalidate_representative_caches() -> None:
    """Etter velg/fjern representant. Kadensen må også nullstilles:
    pinningen kan omklassifisere et produkt som finnes i historikken."""
    global _BASELINE_IDS, _CADENCE_BY_TYPE
    _BASELINE_IDS = None
    _CADENCE_BY_TYPE = None


def _mode_urls(
    cycle: int, top: int, max_per_cat: int, search: str, top_up: bool
) -> tuple[str, str]:
    """Bygg URL-er for modus-bytte som preserverer ikke-default filtre."""
    base: dict[str, str | int] = {}
    if cycle != DEFAULT_CYCLE:
        base["cycle"] = cycle
    if top != DEFAULT_TOP:
        base["top"] = top
    if max_per_cat != DEFAULT_MAX_PER_CAT:
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


def _foreslag_for(cadence_row: pd.Series, cycle: int) -> int:
    """Samme formel som curate: ceil(cycle × snitt-per-event / median)."""
    median = max(float(cadence_row["median_days"]), 1.0)
    return max(1, math.ceil(cycle * float(cadence_row["avg_qty_per_event"]) / median))


def _cart_qty_for(cart: pd.DataFrame, key: str) -> int:
    """Sum antall i kurven for varetypen (0 hvis ingen variant er der).
    Teller per varetype, ikke per produkt-id — samme semantikk som
    compute_diff, så en substituerende variant (buksebleier for bleier)
    regnes som dekning."""
    if cart.empty:
        return 0
    sub = cart[cart["_type"] == key]
    if sub.empty:
        return 0
    return int(sub["quantity"].sum())


def _variants_for_row(lines: pd.DataFrame, key: str) -> list[dict]:
    """Topp varianter for en varetype, formatert for templaten."""
    df = variants_for_type(lines, key, limit=_VARIANT_LIMIT,
                           blocked=blocklist.blocked_ids())
    if df.empty:
        return []
    return [
        {"product_id": int(r["product_id"]), "product_name": str(r["product_name"])}
        for _, r in df.iterrows()
    ]


def _build_row_dict(
    cadence_row: pd.Series,
    pid: int,
    product_name: str,
    category: str,
    cycle: int,
    new_list: bool,
    cart: pd.DataFrame | None,
    baseline_ids: set[int],
    variants: list[dict],
    price_map: dict[int, float] | None = None,
    image_map: dict[int, str] | None = None,
    is_added_variant: bool = False,
    is_chosen: bool = False,
) -> dict:
    """Bygg rad-dict klar for `_list_row.html`, gitt en cadence-rad
    (typenivå) og en konkret pid + navn. Brukes både av hovedtabellen og
    av variant-swap/add-endepunktene.

    `is_added_variant=True` markerer rader som brukeren har lagt til via
    +-knappen — disse får −-knapp i UI for symmetrisk fjerning, og
    flagget bevares gjennom dropdown-swap."""
    forslag = _foreslag_for(cadence_row, cycle)
    if new_list:
        i_kurv = None
        mangler = None
        default_qty = forslag
    else:
        i_kurv = _cart_qty_for(
            cart if cart is not None else _EMPTY_CART, str(cadence_row["key"])
        )
        mangler = max(0, forslag - i_kurv)
        default_qty = mangler
    unit_price = (price_map or {}).get(pid)
    line_cost = unit_price * default_qty if unit_price is not None else None
    return {
        "product_id": pid,
        "image": (image_map or {}).get(pid),
        "unit_price": unit_price,
        "line_cost": line_cost,
        "category": category,
        "key": str(cadence_row["key"]),
        "product_name": product_name,
        "forslag": forslag,
        "i_kurv": i_kurv,
        "mangler": mangler,
        "qty": default_qty,
        "days_since": int(cadence_row["days_since"]) if pd.notna(cadence_row.get("days_since")) else None,
        "last_label": _no_short_date(cadence_row["last"]),
        "is_extra": pid not in baseline_ids,
        "is_added_variant": is_added_variant,
        "is_chosen": is_chosen,
        "variants": variants,
    }


def _engangs_rows(
    search: str, new_list: bool, cart: pd.DataFrame | None,
    price_map: dict[int, float],
) -> list[dict]:
    """Rader for engangsvarer fra katalogsøket (jf. engangsvarer.py).
    Ingen kadens — antallet er det brukeren har lagt inn lokalt. Sist
    betalt pris vinner over søketreff-snapshotet, som for representanter."""
    rows: list[dict] = []
    for item in engangsvarer.list_items():
        if search and search.lower() not in item["name"].lower():
            continue
        pid = item["product_id"]
        qty = item["qty"]
        if new_list:
            i_kurv = None
            mangler = None
            default_qty = qty
        else:
            c = cart if cart is not None else _EMPTY_CART
            sub = c[c["product_id"].astype(int) == pid] if not c.empty else c
            i_kurv = 0 if sub.empty else int(sub["quantity"].sum())
            mangler = max(0, qty - i_kurv)
            default_qty = mangler
        price = price_map.get(pid, item["price"])
        unit_price = float(price) if price is not None else None
        rows.append({
            "product_id": pid,
            "image": item["image"] or None,
            "unit_price": unit_price,
            "line_cost": unit_price * default_qty if unit_price is not None else None,
            "category": "",
            "key": "engangs",
            "product_name": item["name"],
            "forslag": qty,
            "i_kurv": i_kurv,
            "mangler": mangler,
            "qty": default_qty,
            "days_since": None,
            "last_label": "",
            "is_extra": False,
            "is_added_variant": False,
            "is_chosen": False,
            "is_engangs": True,
            "variants": [],
        })
    return rows


def _build_rows(
    lines: pd.DataFrame,
    cycle: int,
    top: int,
    max_per_cat: int,
    search: str,
    new_list: bool,
    top_up: bool,
) -> tuple[list[dict], int, int]:
    chosen = representatives.chosen_representatives()
    ideal = curate(
        lines, list_cycle_days=cycle, top_n=top, max_per_category=max_per_cat,
        blocked=blocklist.blocked_ids(),
        blocked_types=blocklist.blocked_types(),
        chosen=chosen,
    )
    if ideal.empty:
        # Engangsvarer skal vises selv uten kadens-kandidater.
        cart = get_cart() if not new_list else None
        cart_total = int(cart["quantity"].sum()) if cart is not None and not cart.empty else 0
        return _engangs_rows(search, new_list, cart, get_price_map()), cart_total, 0

    baseline_ids = get_baseline_ids()
    price_map = get_price_map()
    image_map = get_image_map()
    if chosen:
        # Katalogvarer finnes ikke i historikken — bruk snapshotet fra
        # søketreffet. setdefault: en ekte sist-betalt-pris vinner.
        price_map = dict(price_map)
        image_map = dict(image_map)
        for c in chosen.values():
            pid = int(c["product_id"])
            if c.get("price") is not None:
                price_map.setdefault(pid, float(c["price"]))
            if c.get("image"):
                image_map.setdefault(pid, str(c["image"]))

    cart_total = 0
    cart: pd.DataFrame | None = None
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
        pid = int(r["product_id"])
        key = str(r["key"])
        is_chosen = (
            key in chosen and int(chosen[key]["product_id"]) == pid
        )
        # Valgt representant: ingen variant-dropdown — katalogvaren
        # finnes ikke blant de historiske variantene.
        variants = [] if is_chosen else _variants_for_row(lines, key)
        row = _build_row_dict(
            cadence_row=r,
            pid=pid,
            product_name=str(r["product_name"]),
            category=str(r["category"]),
            cycle=cycle,
            new_list=new_list,
            cart=cart,
            baseline_ids=baseline_ids,
            variants=variants,
            price_map=price_map,
            image_map=image_map,
            is_chosen=is_chosen,
        )
        if row["is_extra"]:
            extra_count += 1
        rows.append(row)
    rows.extend(_engangs_rows(search, new_list, cart, price_map))
    return rows, cart_total, extra_count


def _list_total(rows: list[dict]) -> float:
    """Sum av radsummene (avrundet per rad, som JS-en) — ca.-total for lista."""
    return float(
        sum(round(r["line_cost"]) for r in rows if r.get("unit_price") is not None)
    )


def _active_pids_from_form(form) -> set[int]:
    """Plukk ut alle pid-er som har en qty-input på siden — uavhengig av
    om de står på 0 eller mer. Brukes til å unngå at variant-add
    dupliserer noe som allerede er der."""
    pids: set[int] = set()
    for k in form.keys():
        if not k.startswith("qty_"):
            continue
        try:
            pids.add(int(k.removeprefix("qty_")))
        except ValueError:
            continue
    return pids


def _render_variant_row(
    request: Request,
    key: str,
    pid: int,
    cycle: int,
    new_list: bool,
    is_added_variant: bool,
) -> HTMLResponse:
    """Bygg + rendre én _list_row.html for (varetype, pid)-kombinasjonen.
    Returnerer tom HTML hvis vi ikke finner kadens for typen eller pid-en."""
    lines = get_lines()
    cadence = get_cadence_by_type()
    sub = cadence[cadence["key"] == key]
    if sub.empty:
        return HTMLResponse("")
    cadence_row = sub.iloc[0]

    variants = _variants_for_row(lines, key)
    # Finn navn + kategori for pid-en. Variant-listen er kuttet til
    # topp 10, så gå tilbake til lines som fallback.
    match = next((v for v in variants if v["product_id"] == pid), None)
    if match:
        product_name = match["product_name"]
        cat_row = lines[lines["product_id"].astype(int) == pid].head(1)
    else:
        cat_row = lines[lines["product_id"].astype(int) == pid].head(1)
        if cat_row.empty:
            return HTMLResponse("")
        product_name = str(cat_row["product_name"].iloc[0])
    category = str(cat_row["category"].iloc[0]) if not cat_row.empty else ""

    cart = get_cart() if not new_list else None
    row = _build_row_dict(
        cadence_row=cadence_row,
        pid=pid,
        product_name=product_name,
        category=category,
        cycle=cycle,
        new_list=new_list,
        cart=cart,
        baseline_ids=get_baseline_ids(),
        variants=variants,
        price_map=get_price_map(),
        image_map=get_image_map(),
        is_added_variant=is_added_variant,
    )
    return templates.TemplateResponse(
        request,
        "_list_row.html",
        {"r": row, "cycle": cycle, "new_list": new_list},
    )


@app.get("/", response_class=RedirectResponse)
def root() -> RedirectResponse:
    return RedirectResponse("/handleliste")


@app.post("/refresh", response_class=HTMLResponse)
def refresh(request: Request) -> HTMLResponse:
    """Tving en frisk fetch fra Oda + invalider cache. Returnerer det nye
    status-fragmentet til HTMX-knappen."""
    global _REFRESH_STATUS
    _REFRESH_STATUS = maybe_refresh_data(force=True)
    if _REFRESH_STATUS.get("refreshed"):
        invalidate_caches()
    return templates.TemplateResponse(
        request, "_refresh_status.html", refresh_status_ctx()
    )


@app.get("/handleliste", response_class=HTMLResponse)
def handleliste(
    request: Request,
    cycle: int = DEFAULT_CYCLE,
    top: int = DEFAULT_TOP,
    max_per_cat: int = DEFAULT_MAX_PER_CAT,
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
            "list_total": _list_total(rows),
            "url_diff": url_diff,
            "url_new_list": url_new_list,
            "blocked_items": blocklist.list_blocked(),
            "blocked_types": blocklist.list_blocked_types(),
        },
    )


@app.get("/handleliste/table", response_class=HTMLResponse)
def handleliste_table(
    request: Request,
    cycle: int = DEFAULT_CYCLE,
    top: int = DEFAULT_TOP,
    max_per_cat: int = DEFAULT_MAX_PER_CAT,
    search: str = "",
    new_list: bool = False,
    top_up: bool = False,
) -> HTMLResponse:
    rows, _, extra_count = _build_rows(
        get_lines(), cycle, top, max_per_cat, search, new_list, top_up
    )
    return templates.TemplateResponse(
        request, "_list_table.html",
        {"rows": rows, "new_list": new_list, "extra_count": extra_count,
         "cycle": cycle, "list_total": _list_total(rows)},
    )


async def _render_body_after_block_change(
    request: Request, notice: dict | None = None
) -> HTMLResponse:
    """Re-rendrer både tabellen og blokk-listen etter en blokk/avblokk.
    Henter filtrene fra form-data (inkludert via hx-include) slik at
    visningen beholder cycle/top/search/etc. `notice` rendres som en
    angre-linje over tabellen (etter × og «skjul hele varetypen»)."""
    form = await request.form()

    def _int(name: str, default: int) -> int:
        try:
            return int(form.get(name) or default)
        except (TypeError, ValueError):
            return default

    def _bool(name: str) -> bool:
        val = form.get(name)
        return str(val).lower() in {"true", "on", "1"}

    cycle = _int("cycle", DEFAULT_CYCLE)
    rows, _, extra_count = _build_rows(
        get_lines(),
        cycle=cycle,
        top=_int("top", DEFAULT_TOP),
        max_per_cat=_int("max_per_cat", DEFAULT_MAX_PER_CAT),
        search=str(form.get("search") or ""),
        new_list=_bool("new_list"),
        top_up=_bool("top_up"),
    )
    return templates.TemplateResponse(
        request,
        "_handleliste_body.html",
        {
            "rows": rows,
            "extra_count": extra_count,
            "new_list": _bool("new_list"),
            "cycle": cycle,
            "list_total": _list_total(rows),
            "blocked_items": blocklist.list_blocked(),
            "blocked_types": blocklist.list_blocked_types(),
            "notice": notice,
        },
    )


@app.post("/handleliste/block", response_class=HTMLResponse)
async def handleliste_block(request: Request) -> HTMLResponse:
    form = await request.form()
    try:
        pid = int(str(form.get("product_id") or ""))
    except ValueError:
        return HTMLResponse("Ugyldig product_id", status_code=400)
    name = str(form.get("name") or "")
    key = str(form.get("key") or "").strip()
    blocklist.block(pid, name=name)
    invalidate_blocklist_caches()
    notice = {
        "kind": "product",
        "product_id": pid,
        "name": name or str(pid),
        "key": key,
    } if key else None
    return await _render_body_after_block_change(request, notice)


@app.post("/handleliste/unblock", response_class=HTMLResponse)
async def handleliste_unblock(request: Request) -> HTMLResponse:
    form = await request.form()
    try:
        pid = int(str(form.get("product_id") or ""))
    except ValueError:
        return HTMLResponse("Ugyldig product_id", status_code=400)
    blocklist.unblock(pid)
    invalidate_blocklist_caches()
    return await _render_body_after_block_change(request)


@app.post("/handleliste/block-type", response_class=HTMLResponse)
async def handleliste_block_type(request: Request) -> HTMLResponse:
    """Blokker en hel varetype — hele typen forsvinner fra forslag, ikke bare
    én variant. `key` er varetype-nøkkelen, `name` en valgfri etikett."""
    form = await request.form()
    key = str(form.get("key") or "").strip()
    if not key:
        return HTMLResponse("Mangler varetype", status_code=400)
    name = str(form.get("name") or "")
    blocklist.block_type(key, name=name)
    invalidate_blocklist_caches()
    return await _render_body_after_block_change(
        request, {"kind": "type", "key": key, "name": name}
    )


@app.post("/handleliste/unblock-type", response_class=HTMLResponse)
async def handleliste_unblock_type(request: Request) -> HTMLResponse:
    form = await request.form()
    key = str(form.get("key") or "").strip()
    if not key:
        return HTMLResponse("Mangler varetype", status_code=400)
    blocklist.unblock_type(key)
    invalidate_blocklist_caches()
    return await _render_body_after_block_change(request)


def _oda_search_ctx(query: str, key: str = "") -> dict:
    """Felles kontekst for _oda_sok.html: søk i Oda-katalogen med vennlig
    feilhåndtering. `price_str` prekalkuleres fordi hx-vals-attributtet
    ikke kan romme Jinja-uttrykk med apostrofer."""
    results: list[dict] = []
    error: str | None = None
    if query:
        try:
            results = search_products(query)
        except httpx.HTTPError as e:
            error = str(e)
    for p in results:
        p["price_str"] = "" if p["price"] is None else f"{p['price']:.2f}"
    return {"results": results, "q": query, "key": key, "error": error}


@app.get("/handleliste/oda-sok", response_class=HTMLResponse)
def handleliste_oda_sok(
    request: Request, q: str = "", search: str = "", key: str = ""
) -> HTMLResponse:
    """Katalogsøk hos Oda. Uten `key`: engangsmodus, treff får
    legg-i-kurv-knapp. Med `key`: byttemodus, treff får velg-knapp som
    gjør produktet til fast representant for varetypen."""
    query = (q or search).strip()
    return templates.TemplateResponse(
        request, "_oda_sok.html", _oda_search_ctx(query, key)
    )


@app.get("/handleliste/bytt", response_class=HTMLResponse)
def handleliste_bytt(request: Request, key: str) -> HTMLResponse:
    """Åpne et byttepanel under en rad: katalogsøk forhåndsutfylt med
    varetypens basenavn, der treff kan velges som fast representant."""
    query = key.split("-", 1)[0]
    return templates.TemplateResponse(
        request, "_bytt_row.html", _oda_search_ctx(query, key)
    )


@app.post("/handleliste/engangs-legg-til", response_class=HTMLResponse)
async def handleliste_engangs_legg_til(request: Request) -> HTMLResponse:
    """Legg en katalogvare på den lokale handlelista som engangsvare.
    Ingenting sendes til Oda før bulk-posten (legg i kurv / send liste)."""
    form = await request.form()
    try:
        pid = int(str(form.get("product_id") or ""))
    except ValueError:
        return HTMLResponse("Ugyldig product_id", status_code=400)
    price_raw = str(form.get("price") or "").strip()
    engangsvarer.add(
        pid,
        name=str(form.get("name") or ""),
        price=float(price_raw) if price_raw else None,
        image=str(form.get("image") or ""),
    )
    return await _render_body_after_block_change(request)


@app.post("/handleliste/engangs-fjern", response_class=HTMLResponse)
async def handleliste_engangs_fjern(request: Request) -> HTMLResponse:
    form = await request.form()
    try:
        pid = int(str(form.get("product_id") or ""))
    except ValueError:
        return HTMLResponse("Ugyldig product_id", status_code=400)
    engangsvarer.remove(pid)
    return await _render_body_after_block_change(request)


@app.post("/handleliste/velg-representant", response_class=HTMLResponse)
async def handleliste_velg_representant(request: Request) -> HTMLResponse:
    """Gjør en katalogvare til fast representant for varetypen (jf.
    representatives.choose) og re-rendre tabellen."""
    form = await request.form()
    key = str(form.get("key") or "").strip()
    if not key:
        return HTMLResponse("Mangler varetype", status_code=400)
    try:
        pid = int(str(form.get("product_id") or ""))
    except ValueError:
        return HTMLResponse("Ugyldig product_id", status_code=400)
    price_raw = str(form.get("price") or "").strip()
    representatives.choose(
        key,
        pid,
        name=str(form.get("name") or ""),
        price=float(price_raw) if price_raw else None,
        image=str(form.get("image") or ""),
    )
    invalidate_representative_caches()
    return await _render_body_after_block_change(request)


@app.post("/handleliste/fjern-representant", response_class=HTMLResponse)
async def handleliste_fjern_representant(request: Request) -> HTMLResponse:
    form = await request.form()
    key = str(form.get("key") or "").strip()
    if not key:
        return HTMLResponse("Mangler varetype", status_code=400)
    representatives.unchoose(key)
    invalidate_representative_caches()
    return await _render_body_after_block_change(request)


@app.post("/handleliste/variant-swap", response_class=HTMLResponse)
async def handleliste_variant_swap(request: Request) -> HTMLResponse:
    """Bytt produkt-variant for en rad. Tar `key`, `pid` (radens gamle
    pid), `cycle` og `new_list` fra formen; valgt option ligger i
    `product_select_<pid>`. Oppslaget må gå via `pid`, ikke første
    `product_select_`-felt i formen: htmx legger ved hele det omsluttende
    skjemaet på POST, så alle radenes selects følger med. Bevarer
    `is_added_variant`-flagget gjennom swap så `−`/`+`-knappen forblir
    riktig."""
    form = await request.form()
    key = str(form.get("key") or "")
    old_pid = str(form.get("pid") or "")
    if not key or not old_pid:
        return HTMLResponse("", status_code=400)
    cycle = int(form.get("cycle") or DEFAULT_CYCLE)
    new_list = str(form.get("new_list") or "").lower() == "true"
    is_added_variant = str(form.get("is_added_variant") or "").lower() == "true"
    try:
        new_pid = int(str(form.get(f"product_select_{old_pid}") or ""))
    except ValueError:
        return HTMLResponse("", status_code=400)
    return _render_variant_row(request, key, new_pid, cycle, new_list, is_added_variant)


@app.post("/handleliste/variant-add", response_class=HTMLResponse)
async def handleliste_variant_add(request: Request) -> HTMLResponse:
    """Legg til en ekstra rad med en variant av samme varetype. Velger
    den mest populære varianten som ikke allerede er aktiv på siden.
    Den nye raden markeres som `is_added_variant=True` så UI viser en
    `−`-knapp for å fjerne den igjen."""
    form = await request.form()
    key = str(form.get("key") or "")
    if not key:
        return HTMLResponse("", status_code=400)
    cycle = int(form.get("cycle") or DEFAULT_CYCLE)
    new_list = str(form.get("new_list") or "").lower() == "true"

    active = _active_pids_from_form(form)
    candidates = _variants_for_row(get_lines(), key)
    next_pid: int | None = next(
        (v["product_id"] for v in candidates if v["product_id"] not in active),
        None,
    )
    if next_pid is None:
        return HTMLResponse(
            '<tr><td colspan="99" class="muted" style="font-size:12px;'
            'padding:6px 10px;">Ingen flere varianter å legge til av denne'
            ' varetypen.</td></tr>'
        )
    return _render_variant_row(request, key, next_pid, cycle, new_list, is_added_variant=True)


def _parse_qty_items(form) -> list[tuple[int, int]]:
    """Plukk ut (product_id, qty)-par fra skjemaet. Hopper over qty<=0
    og ugyldige felt."""
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
    return items


@app.post("/handleliste/create", response_class=HTMLResponse)
async def handleliste_create(request: Request) -> HTMLResponse:
    form = await request.form()
    cycle = int(form.get("cycle") or DEFAULT_CYCLE)
    title = form.get("title") or "Ukehandel"

    items = _parse_qty_items(form)
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
    engangsvarer.remove_posted(pid for pid, _ in items)
    return HTMLResponse(
        f'<div class="alert ok">La til {ok}/{len(items)} varer. '
        f'<a href="https://oda.com/no/account/lists/details/{list_id}/" '
        f'target="_blank">Åpne på Oda →</a></div>'
    )


@app.post("/handleliste/add-to-cart", response_class=HTMLResponse)
async def handleliste_add_to_cart(request: Request) -> HTMLResponse:
    """Legg de avhukede varene rett i handlekurven på Oda. Antallene
    behandles additivt av Odas endepunkt, så vi sender 'mangler' direkte.

    Vi snapshotter kurven før POST, sammenligner med kurven Oda
    returnerer etter, og rapporterer eksplisitt om noen varer ikke
    gikk fullt gjennom (utsolgt, maks-grense, eller annen stille
    capping). Tabellen oppdateres ikke automatisk — det er bevisst,
    så det er lett å sammenholde mot Oda-kurven manuelt."""
    form = await request.form()
    items = _parse_qty_items(form)
    if not items:
        return HTMLResponse(
            '<div class="alert warn">Ingen varer å legge til (alle på 0).</div>'
        )

    try:
        client = build_client()
    except MissingCredentials as e:
        return HTMLResponse(
            f'<div class="alert error">Mangler innlogging på Oda: {e}</div>'
        )

    cart_before = get_cart()
    before: dict[int, int] = {}
    if not cart_before.empty:
        for _, r in cart_before.iterrows():
            pid = int(r["product_id"])
            before[pid] = before.get(pid, 0) + int(r["quantity"])

    after, err = add_to_cart(client, items)
    if err:
        return HTMLResponse(
            f'<div class="alert error">Kunne ikke legge i kurv: {err}</div>'
        )
    invalidate_cart_cache()

    shortfalls: list[tuple[int, int, int]] = []
    actual_total = 0
    for pid, requested in items:
        actual = after.get(pid, 0) - before.get(pid, 0)
        actual_total += max(actual, 0)
        if actual < requested:
            shortfalls.append((pid, requested, max(actual, 0)))

    # Engangsvarer som kom helt i kurven er ferdige — de som ble cappet
    # (utsolgt e.l.) blir stående på lista.
    engangsvarer.remove_posted(
        {pid for pid, _ in items} - {s[0] for s in shortfalls}
    )

    requested_total = sum(q for _, q in items)
    cart_url = '<a href="https://oda.com/no/cart/" target="_blank">Åpne kurven →</a>'

    if not shortfalls:
        vare = "vare" if requested_total == 1 else "varer"
        return HTMLResponse(
            f'<div class="alert ok">La {requested_total} {vare} i handlekurven. '
            f'{cart_url}</div>'
        )

    lines = get_lines()
    name_by_pid = (
        lines.drop_duplicates("product_id")
        .set_index("product_id")["product_name"]
        .to_dict()
    )
    for e in engangsvarer.list_items():
        name_by_pid.setdefault(e["product_id"], e["name"])
    rows_html = "".join(
        f"<li>{name_by_pid.get(pid, f'#{pid}')}: "
        f"ba om {req}, fikk {act}</li>"
        for pid, req, act in shortfalls
    )
    return HTMLResponse(
        f'<div class="alert warn">'
        f'La {actual_total} av {requested_total} varer i handlekurven. '
        f'Disse gikk ikke fullt gjennom (kan være utsolgt eller maks-grense):'
        f'<ul>{rows_html}</ul>'
        f'{cart_url}</div>'
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
            "monthly": innsikt.monthly_spend(orders),
            "staples": innsikt.staples(orders, lines),
            "cuisines": innsikt.cuisine_mix(lines),
            "baby": innsikt.baby_signal(lines),
            "cooking": innsikt.cooking_style(lines),
            "prices": innsikt.price_consciousness(lines),
            "health": innsikt.health(lines),
            "beverages": innsikt.beverages(lines),
            "top_p": innsikt.top_products(lines),
            "top_p_spend": innsikt.top_products_by_spend(lines),
            "top_c": innsikt.top_categories(lines),
            "one_off": innsikt.one_off_purchases(lines),
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


_register_template_globals()
