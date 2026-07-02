"""Henter ordrehistorikk fra oda.com og bygger orders.csv + lines.csv i data/.

Auth + HTTPX-klient + generiske Oda-helpere ligger i oda_client.py — denne
fila er CLI + JSON-parsing + refresh-orkestrering.

Bruk:
    uv run python -m min_oda.fetch_orders                   # prøver standard endepunkter
    uv run python -m min_oda.fetch_orders --url <full-url>  # spesifikt endepunkt
    uv run python -m min_oda.fetch_orders --order <id>      # detaljer for én ordre
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import pandas as pd

from .oda_client import (
    MissingCredentials,
    auth_error_hint,
    build_client,
    console,
    fetch_with_pagination,
    password_auth_configured,
    try_get,
)

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# Endepunkt vi vet virker per mai 2026. Hvis Oda endrer det, finn ny URL
# i Firefox DevTools (Network-fanen) mens du åpner "Mine ordre" og kjør
# scriptet med --url.
ORDERS_ENDPOINT = "https://oda.com/api/v1/orders/"


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

# Oda bruker relative fraser for nylige leveringer — uten denne håndteringen
# faller dagens (og gårsdagens) ordre ut av orders.csv siden den numeriske
# regexen ikke treffer.
_RELATIVE_DAY_OFFSETS = {
    "i dag": 0,
    "i går": 1,
    "i forgårs": 2,
}


def parse_delivery_time(text: str | None, month_label: str | None) -> pd.Timestamp | None:
    """Tolker 'fre 8. mai, 10:08' + månedsetikett ('Mai' eller 'November 2025')
    eller relativ frase ('i dag, 09:10', 'i går, …') til dato."""
    if not text:
        return None
    low = text.lower()
    for phrase, days_back in _RELATIVE_DAY_OFFSETS.items():
        if low.startswith(phrase):
            today = pd.Timestamp.now(tz="Europe/Oslo").normalize()
            return today - pd.Timedelta(days=days_back)

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


def fetch_all(
    client,
    base_url: str = ORDERS_ENDPOINT,
    with_details: bool = True,
) -> int:
    """Henter ordre-listen og per-ordre-detaljer, lagrer som JSON.
    Returnerer antall ordrer."""
    orders = fetch_with_pagination(client, base_url)
    if not orders:
        raise RuntimeError(f"Fant ingen ordrer fra Oda. {auth_error_hint()}")
    save(orders, "orders.json")

    flat: list[dict] = []
    if orders and isinstance(orders[0], dict) and "orders" in orders[0]:
        for group in orders:
            for o in group.get("orders", []):
                o["_month"] = group.get("name")
                flat.append(o)
    else:
        flat = orders

    if with_details:
        details_dir = DATA_DIR / "order_details"
        details_dir.mkdir(exist_ok=True)
        for order in flat:
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
            time.sleep(0.4)

    return len(flat)


def maybe_refresh_data(force: bool = False, max_age_hours: float = 24.0) -> dict:
    """Sjekker alderen på data/orders.json og henter nytt fra Oda om nødvendig.

    Brukes av web-appens lifespan-event og /refresh-endpoint. Fanger feil
    (mangler creds, nettverk, 401) slik at appen ikke krasjer ved oppstart.

    Returnerer dict:
        refreshed: om data faktisk ble hentet på nytt
        data_age_hours: alder på data/orders.json etterpå (None hvis fila mangler)
        error: feilmelding hvis refresh ikke gikk gjennom
    """
    orders_path = DATA_DIR / "orders.json"

    def current_age() -> float | None:
        if not orders_path.exists():
            return None
        return (time.time() - orders_path.stat().st_mtime) / 3600

    if orders_path.exists() and not force:
        age = current_age()
        if age is not None and age < max_age_hours:
            return {"refreshed": False, "data_age_hours": age, "error": None}

    try:
        client = build_client()
    except MissingCredentials as e:
        return {"refreshed": False, "data_age_hours": current_age(), "error": str(e)}

    try:
        fetch_all(client)
    except Exception as e:
        # En bufret passord-sesjon kan ha utløpt før antatt levetid. Slett
        # bufferet og prøv én gang til med tvunget ny innlogging.
        if password_auth_configured():
            from .auth import clear_cached_session

            console.print("[yellow]Henting feilet — prøver ny innlogging mot Oda.[/yellow]")
            clear_cached_session()
            try:
                client = build_client(force_login=True)
                fetch_all(client)
            except Exception as e2:
                return {
                    "refreshed": False,
                    "data_age_hours": current_age(),
                    "error": str(e2),
                }
        else:
            return {"refreshed": False, "data_age_hours": current_age(), "error": str(e)}

    try:
        build_csvs()
    except Exception as e:
        return {
            "refreshed": True,
            "data_age_hours": current_age(),
            "error": f"Hentet ordrer, men CSV-bygging feilet: {e}",
        }

    return {"refreshed": True, "data_age_hours": current_age(), "error": None}


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


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", help="Spesifikk URL å hente (overstyrer kandidatene)")
    p.add_argument("--order", help="Hent detaljer for én ordre-ID")
    p.add_argument("--no-details", action="store_true", help="Hopp over per-ordre-detaljer")
    args = p.parse_args()

    try:
        client = build_client()
    except MissingCredentials as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    if args.order:
        url = f"https://oda.com/api/v1/orders/{args.order}/"
        data = try_get(client, url)
        if data:
            save(data, f"order_{args.order}.json")
        return

    try:
        n = fetch_all(
            client,
            base_url=args.url or ORDERS_ENDPOINT,
            with_details=not args.no_details,
        )
    except RuntimeError as e:
        console.print(
            f"\n[red]{e}[/red]\n\n"
            "[bold]Slik finner du riktig URL:[/bold]\n"
            "  1. Åpne Firefox → oda.com → logg inn → 'Mine ordre'\n"
            "  2. Trykk F12, gå til 'Network'-fanen, filtrer på 'XHR' eller 'Fetch'\n"
            "  3. Last siden på nytt\n"
            "  4. Se etter en request som returnerer JSON med ordrene dine\n"
            "  5. Høyreklikk → Copy → Copy URL\n"
            "  6. Kjør:  uv run python -m min_oda.fetch_orders --url '<URL>'"
        )
        return

    console.print(f"\n[bold green]Hentet {n} ordrer.[/bold green]")
    console.print("\nBygger orders.csv + lines.csv …")
    n_orders, n_lines = build_csvs()
    console.print(
        f"  [green]→[/green] data/orders.csv ({n_orders} ordrer)"
        + (f", data/lines.csv ({n_lines} linjer)" if n_lines else "")
    )
    console.print("\n[bold green]Ferdig.[/bold green] Start web-appen:  uv run min-oda")


if __name__ == "__main__":
    main()
