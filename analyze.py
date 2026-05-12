"""Analyserer ordrene som ble hentet av fetch_orders.py.

Forventer data/orders.json og helst data/order_details/*.json.
Strukturen på Oda sin JSON er ikke offentlig dokumentert, så vi prøver
flere feltnavn og hopper over det vi ikke gjenkjenner.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

console = Console()
DATA_DIR = Path(__file__).parent / "data"


def pick(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def load_orders() -> list[dict]:
    p = DATA_DIR / "orders.json"
    if not p.exists():
        console.print("[red]Mangler data/orders.json — kjør fetch_orders.py først.[/red]")
        raise SystemExit(1)
    raw = json.loads(p.read_text())
    # Strukturen er gruppert (per måned + "Gjeldende bestillinger") — flatt ut.
    if raw and isinstance(raw[0], dict) and "orders" in raw[0]:
        flat = []
        for group in raw:
            for o in group.get("orders", []):
                o["_month_label"] = group.get("name")
                flat.append(o)
        return flat
    return raw


def load_details() -> dict[str, dict]:
    out: dict[str, dict] = {}
    d = DATA_DIR / "order_details"
    if not d.exists():
        return out
    for f in d.glob("*.json"):
        out[f.stem] = json.loads(f.read_text())
    return out


NB_MONTHS = {
    "januar": 1, "februar": 2, "mars": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "desember": 12,
}


def parse_delivery_time(text: str | None, month_label: str | None) -> pd.Timestamp | None:
    """Tolker 'fre 8. mai, 10:08' + månedsetikett ('Mai' eller 'November 2025') til dato."""
    if not text:
        return None
    import re

    # Hent ut dag + månednavn fra delivery_time
    m = re.search(r"(\d{1,2})\.\s*([a-zA-ZæøåÆØÅ]+)", text)
    if not m:
        return None
    day = int(m.group(1))
    mon_name = m.group(2).lower()
    mon_num = NB_MONTHS.get(mon_name)
    if not mon_num:
        return None

    # År: hvis månedsetikett har tall (f.eks. "November 2025"), bruk det.
    # Ellers er det inneværende år (2026 i dette tilfellet).
    year = 2026
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
        rows.append(
            {
                "order_number": o.get("order_number") or "",
                "date": parse_delivery_time(
                    delivery.get("delivery_time"), o.get("_month_label")
                ),
                "delivery_time_text": delivery.get("delivery_time"),
                "total": o.get("gross_amount"),
                "currency": o.get("currency"),
                "status": status.get("title") if isinstance(status, dict) else status,
                "address": delivery.get("delivery_address"),
                "month_label": o.get("_month_label"),
            }
        )
    df = pd.DataFrame(rows)
    if "total" in df:
        df["total"] = pd.to_numeric(df["total"], errors="coerce")
    return df.sort_values("date", na_position="last").reset_index(drop=True)


def extract_lines(detail: dict) -> list[dict]:
    """Henter ut linjevarer. Strukturen er items.item_groups[].items[]
    der item_groups er kategorier."""
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
            rows.append(
                {
                    "product_id": item.get("product_id"),
                    "product_name": item.get("description"),
                    "category": category,
                    "quantity": qty,
                    "unit_price": unit_price,
                    "line_total": gross,
                    "vat_percentage": item.get("vat_percentage"),
                }
            )
    return rows


def summarize(df: pd.DataFrame, lines: pd.DataFrame) -> None:
    if df.empty:
        console.print("[yellow]Ingen ordrer å analysere.[/yellow]")
        return

    console.rule("[bold]Oversikt[/bold]")
    n = len(df)
    total = df["total"].sum(skipna=True)
    avg = df["total"].mean(skipna=True)
    span = ""
    if df["date"].notna().any():
        span = f"  ({df['date'].min().date()} → {df['date'].max().date()})"
    console.print(f"Antall ordrer: [bold]{n}[/bold]{span}")
    if pd.notna(total):
        console.print(f"Totalt brukt:  [bold]{total:,.0f} kr[/bold]")
        console.print(f"Snitt/ordre:   [bold]{avg:,.0f} kr[/bold]")

    if df["date"].notna().any():
        dated = df.dropna(subset=["date"]).copy()
        dated["year"] = dated["date"].dt.year
        dated["month"] = dated["date"].dt.tz_localize(None).dt.to_period("M")

        console.rule("[bold]Per år[/bold]")
        yearly = dated.groupby("year").agg(
            ordrer=("order_number", "count"),
            sum_kr=("total", "sum"),
            snitt=("total", "mean"),
        )
        t = Table()
        t.add_column("År")
        t.add_column("Ordrer", justify="right")
        t.add_column("Sum kr", justify="right")
        t.add_column("Snitt", justify="right")
        for y, row in yearly.iterrows():
            t.add_row(
                str(int(y)),
                f"{int(row['ordrer'])}",
                f"{row['sum_kr']:,.0f}",
                f"{row['snitt']:,.0f}",
            )
        console.print(t)

        console.rule("[bold]Per måned (siste 18)[/bold]")
        monthly = dated.groupby("month").agg(
            ordrer=("order_number", "count"), sum_kr=("total", "sum")
        )
        t = Table()
        t.add_column("Måned")
        t.add_column("Ordrer", justify="right")
        t.add_column("Sum kr", justify="right")
        for m, row in monthly.tail(18).iterrows():
            t.add_row(str(m), f"{int(row['ordrer'])}", f"{row['sum_kr']:,.0f}")
        console.print(t)

    if not lines.empty:
        console.rule("[bold]Top 15 produkter (antall ganger kjøpt)[/bold]")
        top = (
            lines.dropna(subset=["product_name"])
            .groupby("product_name")
            .agg(ganger=("quantity", "count"), enheter=("quantity", "sum"))
            .sort_values("ganger", ascending=False)
            .head(15)
        )
        t = Table()
        t.add_column("Produkt")
        t.add_column("Ganger", justify="right")
        t.add_column("Enheter", justify="right")
        for name, row in top.iterrows():
            t.add_row(str(name)[:60], f"{int(row['ganger'])}", f"{row['enheter']:.0f}")
        console.print(t)

        if lines["category"].notna().any():
            console.rule("[bold]Per kategori[/bold]")
            cats = (
                lines.dropna(subset=["category"])
                .groupby("category")
                .agg(linjer=("product_name", "count"), sum_kr=("line_total", "sum"))
                .sort_values("sum_kr", ascending=False)
                .head(15)
            )
            t = Table()
            t.add_column("Kategori")
            t.add_column("Linjer", justify="right")
            t.add_column("Sum kr", justify="right")
            for cat, row in cats.iterrows():
                t.add_row(str(cat)[:40], f"{int(row['linjer'])}", f"{row['sum_kr']:,.0f}")
            console.print(t)

        console.rule("[bold]Top 15 produkter (sum kr)[/bold]")
        top_kr = (
            lines.dropna(subset=["product_name"])
            .groupby("product_name")
            .agg(ganger=("quantity", "count"), sum_kr=("line_total", "sum"))
            .sort_values("sum_kr", ascending=False)
            .head(15)
        )
        t = Table()
        t.add_column("Produkt")
        t.add_column("Ganger", justify="right")
        t.add_column("Sum kr", justify="right")
        for name, row in top_kr.iterrows():
            t.add_row(str(name)[:60], f"{int(row['ganger'])}", f"{row['sum_kr']:,.0f}")
        console.print(t)

        console.rule("[bold]Nøkkeltall[/bold]")
        unike = lines["product_name"].nunique()
        total_linjer = len(lines)
        console.print(f"Unike produkter:    [bold]{unike}[/bold]")
        console.print(f"Totalt antall linjer:[bold] {total_linjer}[/bold]")
        if df["date"].notna().any():
            n_days = (df["date"].max() - df["date"].min()).days
            if n_days > 0:
                per_uke = len(df) * 7 / n_days
                console.print(f"Frekvens:           [bold]{per_uke:.2f} ordre/uke[/bold]")


def main() -> None:
    orders = load_orders()
    df = to_orders_df(orders)

    details = load_details()
    all_lines: list[dict] = []
    for oid, detail in details.items():
        for row in extract_lines(detail):
            row["order_id"] = oid
            all_lines.append(row)
    lines = pd.DataFrame(all_lines)
    if not lines.empty:
        lines["quantity"] = pd.to_numeric(lines["quantity"], errors="coerce").fillna(1)
        lines["line_total"] = pd.to_numeric(lines["line_total"], errors="coerce")

    df.to_csv(DATA_DIR / "orders.csv", index=False)
    if not lines.empty:
        lines.to_csv(DATA_DIR / "lines.csv", index=False)

    summarize(df, lines)
    console.print(
        f"\n[dim]Lagret: data/orders.csv"
        + (", data/lines.csv" if not lines.empty else "")
        + "[/dim]"
    )


if __name__ == "__main__":
    main()
