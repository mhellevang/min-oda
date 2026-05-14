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
  GET  /api/v1/product-lists/                      — egne lister
  POST /api/v1/product-lists/                      — opprett liste
  POST /api/v1/product-lists/<id>/products/        — legg til varer i liste
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
    if src == ".env":
        return "Cookien i .env er utløpt. Logg inn på oda.com og oppdater verdiene."
    if src == "ukjent":
        return "Logg inn på oda.com i nettleseren og prøv igjen."
    return f"Cookien fra {src} er utløpt. Logg inn på oda.com igjen i {src}."


def build_client() -> httpx.Client:
    global _LAST_AUTH_SOURCE
    load_dotenv()

    cookie_header = os.environ.get("ODA_COOKIE", "").strip()
    sessionid = os.environ.get("ODA_SESSIONID", "").strip()
    csrftoken = os.environ.get("ODA_CSRFTOKEN", "").strip()
    user_agent = os.environ.get(
        "ODA_USER_AGENT",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) Gecko/20100101 Firefox/128.0",
    )

    if cookie_header or sessionid:
        _LAST_AUTH_SOURCE = ".env"
    else:
        from .auth import load_browser_cookies

        preferred = os.environ.get("ODA_BROWSER", "").strip() or None
        loaded = load_browser_cookies(preferred)
        if not loaded:
            hint = f" (prøvde '{preferred}')" if preferred else ""
            raise MissingCredentials(
                f"Fant ingen Oda-cookies{hint}. Logg inn på oda.com i "
                "Firefox/Chrome/Safari/Edge/Brave, eller sett ODA_SESSIONID i .env."
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


_ADD_PRODUCTS_PAYLOAD_SHAPES = [
    # Etter at vi vet hvilken form Oda faktisk aksepterer, behold bare den
    # og slett resten. Inntil noen kjører --create og ser hvilken som svarer
    # 200/201, prøver vi i rekkefølge.
    ("product_id+quantity", lambda items: [{"product_id": pid, "quantity": q} for pid, q in items]),
    ("product+quantity",    lambda items: [{"product": pid, "quantity": q} for pid, q in items]),
    ("product.id+quantity", lambda items: [{"product": {"id": pid}, "quantity": q} for pid, q in items]),
]


def add_products(
    client: httpx.Client, list_id: int, items: list[tuple[int, int]]
) -> int:
    """POST batch av produkter. Returnerer antall vellykkede.

    Oda har ikke dokumentert kontrakten for dette endepunktet, så vi prøver
    flere payload-former. Når en treffer, logges navnet — pin den i
    _ADD_PRODUCTS_PAYLOAD_SHAPES og fjern de andre.
    """
    url = f"https://oda.com/api/v1/product-lists/{list_id}/products/"
    last_response: httpx.Response | None = None
    for name, build in _ADD_PRODUCTS_PAYLOAD_SHAPES:
        r = client.post(url, json=build(items))
        last_response = r
        if r.status_code in (200, 201):
            console.print(f"  [green]✓[/green] payload-form '{name}' virket")
            return len(items)
        if r.status_code == 400:
            console.print(f"  [dim]400 på '{name}' (prøver neste): {r.text[:160]}[/dim]")
            continue
        console.print(f"  [red]{r.status_code}[/red] {r.text[:200]}")
        return 0
    txt = last_response.text[:200] if last_response is not None else ""
    console.print(f"  [yellow]400[/yellow] — ingen payload-form virket: {txt}")
    return 0
