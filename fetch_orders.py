"""Henter ordrehistorikk fra oda.com med session-cookie fra .env og
skriver både JSON (rå) og CSV (orders.csv + lines.csv) til data/.

Bruk:
    uv run fetch_orders.py                   # prøver standard endepunkter
    uv run fetch_orders.py --url <full-url>  # for å hente et spesifikt endepunkt
    uv run fetch_orders.py --order <id>      # detaljer for én ordre
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
import pandas as pd
from dotenv import load_dotenv
from rich.console import Console

console = Console()
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# Endepunkt vi vet virker per mai 2026. Hvis Oda endrer det, finn ny URL
# i Firefox DevTools (Network-fanen) mens du åpner "Mine ordre" og kjør
# scriptet med --url.
ORDERS_ENDPOINT = "https://oda.com/api/v1/orders/"


def build_client() -> httpx.Client:
    load_dotenv()

    cookie_header = os.environ.get("ODA_COOKIE", "").strip()
    sessionid = os.environ.get("ODA_SESSIONID", "").strip()
    csrftoken = os.environ.get("ODA_CSRFTOKEN", "").strip()
    user_agent = os.environ.get(
        "ODA_USER_AGENT",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) Gecko/20100101 Firefox/128.0",
    )

    if not cookie_header and not sessionid:
        console.print(
            "[red]Mangler credentials.[/red] Kopier .env.example til .env og "
            "fyll inn enten ODA_COOKIE (full streng) eller ODA_SESSIONID + ODA_CSRFTOKEN."
        )
        sys.exit(1)

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


def try_get(client: httpx.Client, url: str) -> dict | list | None:
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
        console.print(
            f"  [red]{r.status_code}[/red] — cookien er utløpt eller mangler. "
            "Logg inn på nytt i Firefox og kopier ny sessionid."
        )
        return None

    console.print(f"  [yellow]{r.status_code}[/yellow] {ctype[:60]}")
    return None


def save(payload: dict | list, name: str) -> Path:
    path = DATA_DIR / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    console.print(f"  [green]→[/green] {path.relative_to(Path.cwd())}")
    return path


# ---------- JSON → CSV --------------------------------------------------

NB_MONTHS = {
    "januar": 1, "februar": 2, "mars": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "desember": 12,
}


def parse_delivery_time(text: str | None, month_label: str | None) -> pd.Timestamp | None:
    """Tolker 'fre 8. mai, 10:08' + månedsetikett ('Mai' eller 'November 2025') til dato."""
    if not text:
        return None
    m = re.search(r"(\d{1,2})\.\s*([a-zA-ZæøåÆØÅ]+)", text)
    if not m:
        return None
    day = int(m.group(1))
    mon_num = NB_MONTHS.get(m.group(2).lower())
    if not mon_num:
        return None
    # Antar inneværende år hvis månedsetikett ikke har eksplisitt år.
    year = pd.Timestamp.now().year
    if month_label:
        ym = re.search(r"(\d{4})", month_label)
        if ym:
            year = int(ym.group(1))
    try:
        return pd.Timestamp(year=year, month=mon_num, day=day, tz="Europe/Oslo")
    except ValueError:
        return None


def to_orders_df(orders: list[dict]) -> pd.DataFrame:
    rows = []
    for o in orders:
        delivery = o.get("delivery") or {}
        status = o.get("status") or {}
        rows.append({
            "order_number": o.get("order_number") or "",
            "date": parse_delivery_time(delivery.get("delivery_time"), o.get("_month")),
            "delivery_time_text": delivery.get("delivery_time"),
            "total": o.get("gross_amount"),
            "currency": o.get("currency"),
            "status": status.get("title") if isinstance(status, dict) else status,
            "address": delivery.get("delivery_address"),
            "month_label": o.get("_month"),
        })
    df = pd.DataFrame(rows)
    if "total" in df:
        df["total"] = pd.to_numeric(df["total"], errors="coerce")
    return df.sort_values("date", na_position="last").reset_index(drop=True)


def extract_lines(detail: dict) -> list[dict]:
    """Henter ut linjevarer fra én ordre-detalj-JSON."""
    items_block = detail.get("items") or {}
    item_groups = items_block.get("item_groups") or []

    rows = []
    for group in item_groups:
        if group.get("type") != "category":
            continue
        category = group.get("name")
        for item in group.get("items", []):
            qty = item.get("quantity") or 1
            gross = item.get("gross_amount")
            unit_price = (gross / qty) if (gross and qty) else None
            rows.append({
                "product_id": item.get("product_id"),
                "product_name": item.get("description"),
                "category": category,
                "quantity": qty,
                "unit_price": unit_price,
                "line_total": gross,
                "vat_percentage": item.get("vat_percentage"),
            })
    return rows


def build_csvs() -> tuple[int, int]:
    """Bygg orders.csv og lines.csv fra rå JSON-filer i data/.

    Kalles automatisk på slutten av main() etter at JSON er hentet.
    Returnerer (antall ordrer, antall linjer).
    """
    orders_path = DATA_DIR / "orders.json"
    if not orders_path.exists():
        console.print("[yellow]Mangler data/orders.json — hopper over CSV-bygging.[/yellow]")
        return 0, 0

    raw = json.loads(orders_path.read_text())
    if raw and isinstance(raw[0], dict) and "orders" in raw[0]:
        orders_flat = []
        for group in raw:
            for o in group.get("orders", []):
                o["_month"] = group.get("name")
                orders_flat.append(o)
    else:
        orders_flat = raw

    orders_df = to_orders_df(orders_flat)
    orders_df.to_csv(DATA_DIR / "orders.csv", index=False)

    details_dir = DATA_DIR / "order_details"
    line_rows: list[dict] = []
    if details_dir.exists():
        for f in details_dir.glob("*.json"):
            detail = json.loads(f.read_text())
            for row in extract_lines(detail):
                row["order_id"] = f.stem
                line_rows.append(row)

    lines_df = pd.DataFrame(line_rows)
    if not lines_df.empty:
        lines_df["quantity"] = pd.to_numeric(lines_df["quantity"], errors="coerce").fillna(1)
        lines_df["line_total"] = pd.to_numeric(lines_df["line_total"], errors="coerce")
        lines_df.to_csv(DATA_DIR / "lines.csv", index=False)

    return len(orders_df), len(lines_df)


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
                # ukjent struktur — lagre rå og stopp
                save(data, f"unknown_response_page{page}.json")
                next_url = None
        else:
            next_url = None

        page += 1
        if page > 50:
            console.print("[yellow]Stopper — over 50 sider, sjekk for løkke.[/yellow]")
            break
        time.sleep(0.3)

    return all_results


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", help="Spesifikk URL å hente (overstyrer kandidatene)")
    p.add_argument("--order", help="Hent detaljer for én ordre-ID")
    p.add_argument("--no-details", action="store_true", help="Hopp over per-ordre-detaljer")
    args = p.parse_args()

    client = build_client()

    if args.order:
        url = f"https://oda.com/api/v1/orders/{args.order}/"
        data = try_get(client, url)
        if data:
            save(data, f"order_{args.order}.json")
        return

    used_url = args.url or ORDERS_ENDPOINT
    orders = fetch_with_pagination(client, used_url)

    if not orders:
        console.print(
            "\n[red]Fant ingen ordrer.[/red] Sannsynligvis feil endepunkt.\n\n"
            "[bold]Slik finner du riktig URL:[/bold]\n"
            "  1. Åpne Firefox → oda.com → logg inn → 'Mine ordre'\n"
            "  2. Trykk F12, gå til 'Network'-fanen, filtrer på 'XHR' eller 'Fetch'\n"
            "  3. Last siden på nytt\n"
            "  4. Se etter en request som returnerer JSON med ordrene dine\n"
            "  5. Høyreklikk → Copy → Copy URL\n"
            "  6. Kjør:  uv run fetch_orders.py --url '<URL>'"
        )
        return

    save(orders, "orders.json")

    # Strukturen er gruppert (per måned + "Gjeldende bestillinger") — flatt ut.
    flat: list[dict] = []
    if orders and isinstance(orders[0], dict) and "orders" in orders[0]:
        for group in orders:
            for o in group.get("orders", []):
                o["_month"] = group.get("name")
                flat.append(o)
    else:
        flat = orders

    console.print(
        f"\n[bold green]Hentet {len(flat)} ordrer[/bold green] "
        f"({len(orders)} måneder) fra {used_url}"
    )
    orders = flat

    if args.no_details:
        return

    console.print("\nHenter detaljer per ordre …")
    details_dir = DATA_DIR / "order_details"
    details_dir.mkdir(exist_ok=True)

    for i, order in enumerate(orders, 1):
        order_id = (
            order.get("order_number")
            or order.get("id")
            or order.get("order_id")
            or order.get("number")
        )
        if not order_id:
            continue
        out = details_dir / f"{order_id}.json"
        if out.exists():
            continue

        url = order.get("url") or f"https://oda.com/api/v1/orders/{order_id}/"
        data = try_get(client, url)
        if data:
            out.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        if i % 5 == 0:
            console.print(f"  {i}/{len(orders)}")
        time.sleep(0.4)

    console.print("\nBygger orders.csv + lines.csv …")
    n_orders, n_lines = build_csvs()
    console.print(
        f"  [green]→[/green] data/orders.csv ({n_orders} ordrer)"
        + (f", data/lines.csv ({n_lines} linjer)" if n_lines else "")
    )
    console.print("\n[bold green]Ferdig.[/bold green] Start web-appen:  make web")


if __name__ == "__main__":
    main()
