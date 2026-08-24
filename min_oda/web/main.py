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

from . import auth, lager
from .. import (
    blocklist,
    engangsvarer,
    forslag,
    innsikt,
    innsikt_llm,
    llm,
    representatives,
)
from ..fetch_orders import maybe_refresh_data
from ..handleliste import (
    DEFAULT_CYCLE,
    DEFAULT_MAX_PER_CAT,
    DEFAULT_TOP,
    Liste,
    Valg,
    bygg,
    kort_dato,
    variant_rad,
    varianter_for,
)
from ..oda_client import (
    add_products,
    add_to_cart,
    build_client,
    create_list,
    search_products,
)

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
    templates.env.globals["llm_enabled"] = llm.enabled
    templates.env.globals["llm_provider"] = llm.provider_label
    templates.env.filters["format_age"] = format_age
    templates.env.filters["format_days_ago"] = format_days_ago
    templates.env.filters["format_kr"] = format_kr
    templates.env.filters["format_int"] = format_int
    templates.env.filters["format_dato"] = kort_dato


# Registrert i bunnen av modulen, etter at refresh_status_ctx er definert.

_REFRESH_STATUS: dict = {
    "refreshed": False,
    "data_age_hours": None,
    "error": None,
}

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


def _mode_urls(valg: Valg) -> tuple[str, str]:
    """Bygg URL-er for modus-bytte som preserverer ikke-default filtre."""
    base: dict[str, str | int] = {}
    if valg.cycle != DEFAULT_CYCLE:
        base["cycle"] = valg.cycle
    if valg.top != DEFAULT_TOP:
        base["top"] = valg.top
    if valg.max_per_cat != DEFAULT_MAX_PER_CAT:
        base["max_per_cat"] = valg.max_per_cat
    if valg.search:
        base["search"] = valg.search

    diff_params = dict(base)
    if valg.top_up:
        diff_params["top_up"] = "true"
    new_list_params = dict(base, new_list="true")

    url_diff = "/handleliste"
    if diff_params:
        url_diff = f"{url_diff}?{urlencode(diff_params)}"
    url_new_list = f"/handleliste?{urlencode(new_list_params)}"
    return url_diff, url_new_list


def _liste(valg: Valg) -> Liste:
    """Handlelista for valgte filtre (jf. handleliste.bygg)."""
    return bygg(
        lager.lines(),
        valg,
        kurv=None if valg.new_list else lager.kurv(),
        kilder=lager.kilder(),
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
    request: Request, key: str, pid: int, valg: Valg, is_added_variant: bool
) -> HTMLResponse:
    """Rendre én _list_row.html for (varetype, pid). Tom HTML hvis vi ikke
    finner kadens for typen eller pid-en."""
    rad = variant_rad(
        lager.lines(),
        lager.kadens(),
        key,
        pid,
        valg,
        kurv=None if valg.new_list else lager.kurv(),
        kilder=lager.kilder(),
        is_added_variant=is_added_variant,
    )
    if rad is None:
        return HTMLResponse("")
    return templates.TemplateResponse(
        request,
        "_list_row.html",
        {"r": rad, "cycle": valg.cycle, "new_list": valg.new_list},
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
        lager.endret(lager.DATA)
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
    valg = Valg(cycle=cycle, top=top, max_per_cat=max_per_cat,
                search=search, new_list=new_list, top_up=top_up)
    liste = _liste(valg)
    url_diff, url_new_list = _mode_urls(valg)
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
            "rows": liste.rader,
            "cart_total": liste.kurv_antall,
            "extra_count": liste.ekstra_antall,
            "list_total": liste.total,
            "url_diff": url_diff,
            "url_new_list": url_new_list,
            "blocked_items": blocklist.list_blocked(),
            "blocked_types": blocklist.list_blocked_types(),
            **_forslag_ctx(auto_start=True),
        },
    )


def _start_forslag_jobb() -> None:
    """Start bakgrunnsgenerering av LLM-forslag. Grunnlaget er den fulle
    kuraterte lista (uavhengig av kurv-diffen), så tipsene ikke avhenger av
    hva som tilfeldigvis mangler akkurat nå."""
    liste = _liste(Valg(new_list=True))
    forslag.start_bakgrunnsjobb(liste.rader, lager.lines())


def _forslag_ctx(auto_start: bool = False) -> dict:
    """Template-kontekst for _llm_forslag.html. `auto_start=True` (sidelast)
    sparker i gang en generering hvis cachen er over et døgn gammel — innen
    brukeren har jobbet seg gjennom lista er den typisk ferdig."""
    if not llm.enabled():
        return {"forslag": None, "forslag_kjorer": False, "forslag_feil": None}
    if auto_start and not forslag.er_i_gang() and not forslag.er_ferskt():
        _start_forslag_jobb()
    return {
        "forslag": forslag.load_forslag(),
        "forslag_kjorer": forslag.er_i_gang(),
        "forslag_feil": forslag.siste_feil(),
    }


@app.get("/handleliste/llm-forslag", response_class=HTMLResponse)
def handleliste_llm_forslag_status(request: Request) -> HTMLResponse:
    """Polles av fragmentet mens genereringen kjører."""
    return templates.TemplateResponse(
        request, "_llm_forslag.html", _forslag_ctx()
    )


@app.post("/handleliste/llm-forslag", response_class=HTMLResponse)
def handleliste_llm_forslag(request: Request) -> HTMLResponse:
    """Tving en ny generering (Oppdater-knappen), uavhengig av cache-alder.
    Returnerer med en gang — fragmentet poller til jobben er ferdig."""
    if llm.enabled() and not forslag.er_i_gang():
        _start_forslag_jobb()
    return templates.TemplateResponse(
        request, "_llm_forslag.html", _forslag_ctx()
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
    liste = _liste(Valg(cycle=cycle, top=top, max_per_cat=max_per_cat,
                        search=search, new_list=new_list, top_up=top_up))
    return templates.TemplateResponse(
        request, "_list_table.html",
        {"rows": liste.rader, "new_list": new_list,
         "extra_count": liste.ekstra_antall, "cycle": cycle,
         "list_total": liste.total},
    )


async def _render_body_after_block_change(
    request: Request, notice: dict | None = None
) -> HTMLResponse:
    """Re-rendrer både tabellen og blokk-listen etter en blokk/avblokk.
    Henter filtrene fra form-data (inkludert via hx-include) slik at
    visningen beholder cycle/top/search/etc. `notice` rendres som en
    angre-linje over tabellen (etter × og «skjul hele varetypen»)."""
    valg = Valg.fra_form(await request.form())
    liste = _liste(valg)
    return templates.TemplateResponse(
        request,
        "_handleliste_body.html",
        {
            "rows": liste.rader,
            "extra_count": liste.ekstra_antall,
            "new_list": valg.new_list,
            "cycle": valg.cycle,
            "list_total": liste.total,
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
    lager.endret(lager.BLOKKERING)
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
    lager.endret(lager.BLOKKERING)
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
    lager.endret(lager.BLOKKERING)
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
    lager.endret(lager.BLOKKERING)
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
    lager.endret(lager.REPRESENTANT)
    return await _render_body_after_block_change(request)


@app.post("/handleliste/fjern-representant", response_class=HTMLResponse)
async def handleliste_fjern_representant(request: Request) -> HTMLResponse:
    form = await request.form()
    key = str(form.get("key") or "").strip()
    if not key:
        return HTMLResponse("Mangler varetype", status_code=400)
    representatives.unchoose(key)
    lager.endret(lager.REPRESENTANT)
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
    is_added_variant = str(form.get("is_added_variant") or "").lower() == "true"
    try:
        new_pid = int(str(form.get(f"product_select_{old_pid}") or ""))
    except ValueError:
        return HTMLResponse("", status_code=400)
    return _render_variant_row(
        request, key, new_pid, Valg.fra_form(form), is_added_variant
    )


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
    active = _active_pids_from_form(form)
    candidates = varianter_for(lager.lines(), key)
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
    return _render_variant_row(
        request, key, next_pid, Valg.fra_form(form), is_added_variant=True
    )


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

    cart_before = lager.kurv()
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
    lager.endret(lager.KURV)

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

    lines = lager.lines()
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


def _innsikt_llm_ctx(auto_start: bool = False) -> dict:
    """Template-kontekst for _innsikt_llm.html. Samme mønster som
    _forslag_ctx: sidelast starter en generering hvis cachen er gammel."""
    if not llm.enabled():
        return {"innsikt_llm": None, "innsikt_llm_kjorer": False,
                "innsikt_llm_feil": None}
    if auto_start and not innsikt_llm.er_i_gang() and not innsikt_llm.er_ferskt():
        innsikt_llm.start_bakgrunnsjobb(lager.lines())
    return {
        "innsikt_llm": innsikt_llm.load_innsikt(),
        "innsikt_llm_kjorer": innsikt_llm.er_i_gang(),
        "innsikt_llm_feil": innsikt_llm.siste_feil(),
    }


@app.get("/innsikt/llm", response_class=HTMLResponse)
def innsikt_llm_status(request: Request) -> HTMLResponse:
    """Polles av fragmentet mens genereringen kjører."""
    return templates.TemplateResponse(
        request, "_innsikt_llm.html", _innsikt_llm_ctx()
    )


@app.post("/innsikt/llm", response_class=HTMLResponse)
def innsikt_llm_regenerer(request: Request) -> HTMLResponse:
    """Tving en ny generering (Oppdater-knappen), uavhengig av cache-alder."""
    if llm.enabled() and not innsikt_llm.er_i_gang():
        innsikt_llm.start_bakgrunnsjobb(lager.lines())
    return templates.TemplateResponse(
        request, "_innsikt_llm.html", _innsikt_llm_ctx()
    )


@app.get("/innsikt", response_class=HTMLResponse)
def innsikt_page(request: Request, q: str = "") -> HTMLResponse:
    orders, lines = lager.orders_og_lines()
    pairs, counts, name_map, n_orders = lager.basket_par()
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
            **_innsikt_llm_ctx(auto_start=True),
        },
    )


@app.get("/innsikt/basket-lookup", response_class=HTMLResponse)
def innsikt_basket_lookup(request: Request, q: str = "") -> HTMLResponse:
    pairs, counts, name_map, n_orders = lager.basket_par()
    basket_lookup = (
        innsikt.basket_for_product(pairs, name_map, counts, n_orders, q)
        if q
        else None
    )
    return templates.TemplateResponse(
        request, "_basket_lookup.html", {"basket_lookup": basket_lookup}
    )


_register_template_globals()
