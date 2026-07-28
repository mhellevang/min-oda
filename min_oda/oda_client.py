"""Authenticated HTTP-klient mot oda.com og smale wrappers rundt de
Oda-endepunktene vi bruker. Felles for fetch_orders-scriptet, build_list,
cart_diff og web-appen.

Auth: cookies fra .env (manuell fallback) eller fra nettleserens cookie-
store via rookiepy. Hvilken kilde vi brukte sist eksponeres som
last_auth_source() — brukes til feilmeldinger.

Oda-endepunkter vi snakker med:
  GET  /api/v1/orders/                            (paginert) — ordreliste
  GET  /api/v1/orders/<id>/                        — ordredetaljer
  GET  /api/v1/cart/?group-by=categories           — handlekurv
  POST /api/v1/cart/items/                         — legg varer i kurv (additivt)
  GET  /api/v1/product-lists/                      — egne lister
  POST /api/v1/product-lists/                      — opprett liste
  POST /api/v1/product-lists/<id>/products/        — legg til varer i liste
  GET  /api/v1/search/mixed/?q=<søk>               — katalogsøk (åpent, uten innlogging)
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
from dotenv import load_dotenv
from rich.console import Console

console = Console()

LIST_URL = "https://oda.com/api/v1/product-lists/"


class MissingCredentials(RuntimeError):
    """Reises når vi verken finner cookies i .env eller i en innlogget nettleser."""


_LAST_AUTH_SOURCE = "ukjent"


def last_auth_source() -> str:
    """Hvor kom credentials sist fra: '.env', 'firefox', 'chrome', osv.
    Brukes til feilmeldinger som peker brukeren tilbake til riktig sted."""
    return _LAST_AUTH_SOURCE


def auth_error_hint() -> str:
    """Brukervennlig hint om hvor man må logge inn på nytt, basert på
    hvilken auth-kilde vi sist brukte. Brukes av CLI-feilmeldinger og
    av web-bannerets refresh-status."""
    src = last_auth_source()
    if src.startswith("passord"):
        return (
            "Innloggingen mot Oda gikk ikke gjennom. Sjekk ODA_USERNAME/ODA_PASSWORD, "
            "eller om Oda krever ny bekreftelse (captcha/2FA) for kontoen."
        )
    if src == ".env":
        return "Cookien i .env er utløpt. Logg inn på oda.com og oppdater verdiene."
    if src == "ukjent":
        return "Logg inn på oda.com i nettleseren og prøv igjen."
    return f"Cookien fra {src} er utløpt. Logg inn på oda.com igjen i {src}."


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
)


def password_auth_configured() -> bool:
    """True hvis ODA_USERNAME + ODA_PASSWORD er satt. Brukes til å avgjøre om
    en mislykket henting skal prøve ny innlogging med tvunget re-login."""
    load_dotenv()
    return bool(
        os.environ.get("ODA_USERNAME", "").strip()
        and os.environ.get("ODA_PASSWORD", "").strip()
    )


def build_client(force_login: bool = False) -> httpx.Client:
    """Bygg en autentisert httpx-klient mot Oda.

    Kilde-rekkefølge:
      1. Manuell cookie i .env (ODA_COOKIE / ODA_SESSIONID).
      2. Passord-login (ODA_USERNAME + ODA_PASSWORD), sesjon buffres til disk.
      3. Cookies fra en innlogget nettleser (rookiepy).

    force_login=True hopper over sesjonsbufferet og logger inn på nytt — brukes
    når en henting feilet fordi den bufrede sesjonen kan ha utløpt før tiden.
    """
    global _LAST_AUTH_SOURCE
    load_dotenv()

    cookie_header = os.environ.get("ODA_COOKIE", "").strip()
    sessionid = os.environ.get("ODA_SESSIONID", "").strip()
    csrftoken = os.environ.get("ODA_CSRFTOKEN", "").strip()
    user_agent = os.environ.get("ODA_USER_AGENT", DEFAULT_USER_AGENT)
    username = os.environ.get("ODA_USERNAME", "").strip()
    password = os.environ.get("ODA_PASSWORD", "").strip()

    if cookie_header or sessionid:
        _LAST_AUTH_SOURCE = ".env"
    elif username and password:
        from .auth import LoginFailed, get_session

        try:
            session, from_cache = get_session(
                username, password, user_agent, force=force_login
            )
        except LoginFailed as e:
            raise MissingCredentials(f"Innlogging mot Oda feilet: {e}") from e
        sessionid = session["sessionid"]
        csrftoken = session.get("csrftoken", "")
        _LAST_AUTH_SOURCE = "passord (bufret)" if from_cache else "passord"
    else:
        from .auth import load_browser_cookies

        preferred = os.environ.get("ODA_BROWSER", "").strip() or None
        loaded = load_browser_cookies(preferred)
        if not loaded:
            hint = f" (prøvde '{preferred}')" if preferred else ""
            raise MissingCredentials(
                f"Fant ingen Oda-cookies{hint}. Logg inn på oda.com i "
                "Firefox/Chrome/Safari/Edge/Brave, sett ODA_USERNAME/ODA_PASSWORD, "
                "eller sett ODA_SESSIONID i .env."
            )
        cookies_dict, browser_name = loaded
        sessionid = cookies_dict.get("sessionid", "")
        csrftoken = cookies_dict.get("csrftoken", "")
        _LAST_AUTH_SOURCE = browser_name

    cookies: dict[str, str] = {}
    if sessionid:
        cookies["sessionid"] = sessionid
    if csrftoken:
        cookies["csrftoken"] = csrftoken

    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
        "Referer": "https://oda.com/no/account/orders/",
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    if csrftoken:
        headers["X-CSRFToken"] = csrftoken

    return httpx.Client(
        headers=headers,
        cookies=cookies if not cookie_header else None,
        timeout=30.0,
        follow_redirects=True,
    )


def try_get(client: httpx.Client, url: str) -> Any:
    """GET med vennlig feilrapportering til stdout. Returnerer parsed JSON
    eller None ved feil."""
    console.print(f"[dim]GET {url}[/dim]")
    try:
        r = client.get(url)
    except httpx.HTTPError as e:
        console.print(f"  [red]nettverksfeil:[/red] {e}")
        return None

    ctype = r.headers.get("content-type", "")
    if r.status_code == 200 and "json" in ctype:
        console.print(f"  [green]200 OK[/green] ({len(r.content)} bytes)")
        return r.json()

    if r.status_code in (401, 403):
        console.print(f"  [red]{r.status_code}[/red] uautorisert")
        return None

    console.print(f"  [yellow]{r.status_code}[/yellow] {ctype[:60]}")
    return None


def fetch_with_pagination(client: httpx.Client, url: str) -> list:
    """Følger DRF-stil paginering (next-felt) og returnerer alle results."""
    all_results: list = []
    page = 1
    next_url: str | None = url

    while next_url:
        data = try_get(client, next_url)
        if data is None:
            break

        if isinstance(data, list):
            all_results.extend(data)
            next_url = None
        elif isinstance(data, dict):
            results = data.get("results") or data.get("orders") or data.get("data")
            if isinstance(results, list):
                all_results.extend(results)
                if data.get("has_more") is False:
                    next_url = None
                else:
                    next_url = (
                        data.get("get_more_url")
                        or data.get("next")
                        or data.get("next_page_url")
                    )
            else:
                console.print(
                    f"[yellow]Ukjent JSON-struktur på side {page} — stopper.[/yellow]"
                )
                next_url = None
        else:
            next_url = None

        page += 1
        if page > 50:
            console.print("[yellow]Stopper — over 50 sider, sjekk for løkke.[/yellow]")
            break
        time.sleep(0.3)

    return all_results


def create_list(client: httpx.Client, title: str, description: str) -> dict | None:
    payload = {"title": title, "description": description}
    r = client.post(LIST_URL, json=payload)
    if r.status_code not in (200, 201):
        console.print(
            f"[red]Kunne ikke opprette liste:[/red] {r.status_code} {r.text[:300]}"
        )
        return None
    data = r.json()
    console.print(
        f"[green]✓[/green] Opprettet liste id={data.get('id')} \"{data.get('title')}\""
    )
    return data


def add_products(
    client: httpx.Client, list_id: int, items: list[tuple[int, int]]
) -> int:
    """POST batch av produkter. Returnerer antall vellykkede."""
    url = f"https://oda.com/api/v1/product-lists/{list_id}/products/"
    payload = [{"product_id": pid, "quantity": q} for pid, q in items]
    r = client.post(url, json=payload)
    if r.status_code in (200, 201):
        return len(items)
    console.print(f"  [red]{r.status_code}[/red] {r.text[:200]}")
    return 0


SEARCH_URL = "https://oda.com/api/v1/search/mixed/"


def search_products(query: str, limit: int = 12) -> list[dict]:
    """Søk i Odas katalog. Endepunktet er åpent, så ingen autentisert
    klient trengs. Returnerer tilgjengelige produkter som dicts med
    product_id, name, price (kr eller None) og image (thumbnail-URL
    eller None). Ikke-produkter (kategorier, kampanjer) og utsolgte
    varer filtreres bort."""
    r = httpx.get(
        SEARCH_URL,
        params={"q": query},
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
        timeout=15.0,
    )
    r.raise_for_status()
    out: list[dict] = []
    for item in r.json().get("items", []):
        if item.get("type") != "product":
            continue
        a = item.get("attributes") or {}
        if not (a.get("availability") or {}).get("is_available", True):
            continue
        images = a.get("images") or []
        image = None
        if images:
            image = ((images[0].get("thumbnail") or {}).get("url")
                     or (images[0].get("large") or {}).get("url"))
        try:
            price = float(a["gross_price"])
        except (KeyError, TypeError, ValueError):
            price = None
        out.append({
            "product_id": int(a["id"]),
            "name": a.get("full_name") or a.get("name") or "",
            "price": price,
            "image": image,
        })
        if len(out) >= limit:
            break
    return out


CART_ITEMS_URL = "https://oda.com/api/v1/cart/items/"


def add_to_cart(
    client: httpx.Client, items: list[tuple[int, int]]
) -> tuple[dict[int, int], str | None]:
    """POST batch av produkter rett i handlekurven. Returnerer
    (kvantum per produkt-id i kurven *etter* POST-en, evt_feilmelding).

    Oda behandler quantity additivt — qty=1 inkrementerer med 1. Vi
    sender derfor 'mangler' (eller 'foreslått antall'), ikke ønsket
    totalantall.

    Selve respons-bodyen er hele kurv-objektet — kalleren kan diffe
    mot et 'før'-snapshot for å oppdage at f.eks. en utsolgt vare ikke
    faktisk ble lagt til (Oda returnerer 200 også når enkeltvarer
    droppes stille).
    """
    payload = {
        "items": [{"product_id": pid, "quantity": q} for pid, q in items]
    }
    r = client.post(CART_ITEMS_URL, json=payload)
    if r.status_code not in (200, 201):
        console.print(f"  [red]{r.status_code}[/red] {r.text[:200]}")
        return {}, f"{r.status_code}: {r.text[:200]}"

    after: dict[int, int] = {}
    for group in r.json().get("groups", []):
        for it in group.get("items", []):
            pid = (it.get("product") or {}).get("id")
            if pid is None:
                continue
            after[int(pid)] = after.get(int(pid), 0) + int(it.get("quantity") or 0)
    return after, None
