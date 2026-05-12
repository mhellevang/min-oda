"""Bygger en kuratert handleliste basert på faktiske handlevaner og oppretter
den på oda.com via /api/v1/product-lists/.

Bygger på `restock.compute_cadence(by_type=True)` — for hver varetype med
stabil kjøpsrytme velges sist kjøpte produkt som representant, og antall
beregnes fra listesyklus / median-intervall.

Bruk:
    uv run build_list.py                  # forhåndsvisning, oppretter ingenting
    uv run build_list.py --create         # oppretter listen på oda.com
    uv run build_list.py --title "X"      # egendefinert tittel
    uv run build_list.py --cycle 7        # ukentlig syklus (default 14 d)
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import httpx
import pandas as pd
from rich.console import Console
from rich.table import Table

from fetch_orders import build_client
from data_loader import load_lines, load_orders
from product_types import product_type
from restock import compute_cadence

console = Console()

LIST_URL = "https://oda.com/api/v1/product-lists/"

# Kategorier vi vil ha med i en god ukehandel — rangert etter familiens
# fokus: barn først, så middag, så frokost/lunsj/snacks.
CATEGORY_PRIORITY = [
    "Bleier",
    "Småbarn 0-3",
    "Babymat 4 mnd.",
    "Babymat 6 mnd.",
    "Frukt og grønt",
    "Brød og bakeri",
    "Meieri",
    "Yoghurt",
    "Egg",
    "Pålegg",
    "Ost",
    "Kjøtt",
    "Fisk og skalldyr",
    "Pasta og ris",
    "Ferdigmat",
    "Krydder og smaker",
    "Frukost",
    "Snacks",
    "Drikke",
    "Vann",
    "Kaffe og te",
    "Hygiene",
    "Vaskemiddel",
    "Husholdning",
]

def load() -> pd.DataFrame:
    orders = load_orders()
    lines = load_lines(orders)
    return lines.dropna(subset=["product_id", "product_name", "date"])


def curate(
    lines: pd.DataFrame,
    list_cycle_days: int = 14,
    top_n: int = 40,
    max_per_category: int = 8,
) -> pd.DataFrame:
    """Bygg handleliste basert på restock-kadens per varetype.

    For hver varetype som har en stabil kjøpsrytme (jf. `compute_cadence`)
    velges det sist kjøpte produktet som representant, og antall settes til
    `ceil(syklus / median-intervall)`. Melk med 7-dagers kadens får da
    qty=2 på en 14-dagers liste; brød med 5-dagers kadens får qty=3.

    Vi arver alle kadens-filtre fra restock.py: pant/gavekort, forlatte
    produkter, sjeldne kjøp (median > 90 d), størrelses-kodede varer som
    vokses ut av. Produkter uten klassifisert varetype i `product_types`
    havner ikke på listen.
    """
    cadence = compute_cadence(lines, by_type=True)
    if cadence.empty:
        return cadence

    # Finn representativt produkt per varetype: produktet med flest distinkte
    # ordrer (ikke det sist kjøpte — siste kan være en engangsvariant som
    # ikke representerer den faste rytmen).
    df = lines.dropna(subset=["product_id", "product_name", "category"]).copy()
    df["product_id"] = df["product_id"].astype(int)
    df["_type"] = df.apply(
        lambda r: product_type(r["product_name"], r.get("category"), r["product_id"]),
        axis=1,
    )
    df = df.dropna(subset=["_type"])
    rep = (
        df.groupby(["_type", "product_id", "product_name", "category"])["order_id"]
        .nunique()
        .reset_index(name="n_orders")
        .sort_values(["_type", "n_orders"], ascending=[True, False])
        .groupby("_type")
        .head(1)
        .drop(columns="n_orders")
        .reset_index(drop=True)
        .rename(columns={"_type": "key"})
    )

    out = cadence.drop(columns=["product_name", "category"]).merge(rep, on="key")

    out["foreslått_antall"] = out["median_days"].apply(
        lambda m: max(1, math.ceil(list_cycle_days / max(m, 1)))
    )

    def prio(cat: str) -> int:
        try:
            return CATEGORY_PRIORITY.index(cat)
        except ValueError:
            return 999

    out["_prio"] = out["category"].map(prio)
    out = out.sort_values(["_prio", "n_buys"], ascending=[True, False])

    keep = []
    cat_count: dict[str, int] = {}
    for _, row in out.iterrows():
        cat = row["category"]
        if cat_count.get(cat, 0) >= max_per_category:
            continue
        keep.append(row)
        cat_count[cat] = cat_count.get(cat, 0) + 1
        if len(keep) >= top_n:
            break

    return pd.DataFrame(keep).drop(columns="_prio").reset_index(drop=True)


def show(curated: pd.DataFrame, cycle: int) -> None:
    t = Table(title=f"Handleliste — {len(curated)} varetyper · syklus {cycle} d")
    t.add_column("#", justify="right")
    t.add_column("Kategori")
    t.add_column("Varetype")
    t.add_column("Produkt (eksempel)")
    t.add_column("Antall", justify="right")
    t.add_column("Kadens", justify="right")
    t.add_column("Sist kjøpt")
    for i, row in curated.iterrows():
        median = int(round(row["median_days"]))
        t.add_row(
            str(i + 1),
            str(row["category"])[:22],
            str(row["key"]).capitalize()[:22],
            str(row["product_name"])[:45],
            str(row["foreslått_antall"]),
            f"hver {median}. d",
            str(row["last"].date()) if pd.notna(row["last"]) else "—",
        )
    console.print(t)


def create_list(
    client: httpx.Client, title: str, description: str
) -> dict | None:
    payload = {"title": title, "description": description}
    r = client.post(LIST_URL, json=payload)
    if r.status_code not in (200, 201):
        console.print(f"[red]Kunne ikke opprette liste:[/red] {r.status_code} {r.text[:300]}")
        return None
    data = r.json()
    console.print(f"[green]✓[/green] Opprettet liste id={data.get('id')} \"{data.get('title')}\"")
    return data


def add_products(
    client: httpx.Client, list_id: int, items: list[tuple[int, int]]
) -> int:
    """POST batch av produkter. Returnerer antall vellykkede."""
    url = f"https://oda.com/api/v1/product-lists/{list_id}/products/"
    # Endepunktet venter et array — prøv noen rimelige former
    candidates = [
        [{"product_id": pid, "quantity": q} for pid, q in items],
        [{"product": pid, "quantity": q} for pid, q in items],
        [{"product": {"id": pid}, "quantity": q} for pid, q in items],
    ]
    for payload in candidates:
        r = client.post(url, json=payload)
        if r.status_code in (200, 201):
            return len(items)
        if r.status_code == 400:
            console.print(f"  [dim]400 (prøver neste form): {r.text[:160]}[/dim]")
            continue
        console.print(f"  [red]{r.status_code}[/red] {r.text[:200]}")
        return 0
    console.print(f"  [yellow]400[/yellow] — ingen payload-form virket: {r.text[:200]}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--create", action="store_true", help="Opprett listen på oda.com")
    p.add_argument("--title", default="Ukehandel — familien", help="Listetittel")
    p.add_argument(
        "--description",
        default="Faste varer basert på kjøpskadens per varetype",
    )
    p.add_argument("--cycle", type=int, default=14,
                   help="Listesyklus i dager — qty per vare = "
                        "ceil(syklus / median-intervall)")
    p.add_argument("--top", type=int, default=40, help="Maks antall varetyper")
    p.add_argument("--max-per-category", type=int, default=8,
                   help="Maks antall varetyper per Oda-kategori")
    args = p.parse_args()

    lines = load()
    curated = curate(
        lines,
        list_cycle_days=args.cycle,
        top_n=args.top,
        max_per_category=args.max_per_category,
    )
    show(curated, cycle=args.cycle)

    if not args.create:
        console.print(
            "\n[dim]Forhåndsvisning. Kjør med [bold]--create[/bold] for å opprette listen på oda.com.[/dim]"
        )
        return

    client = build_client()

    # Sjekk at vi ikke allerede har en liste med samme tittel
    existing = client.get(LIST_URL).json().get("results", [])
    if any(ls["title"] == args.title for ls in existing):
        console.print(
            f"[yellow]Liste \"{args.title}\" finnes allerede.[/yellow] "
            "Bruk --title for å velge et annet navn."
        )
        return

    result = create_list(client, args.title, args.description)
    if not result:
        return
    list_id = result["id"]

    console.print(f"\nLegger til {len(curated)} varer …")
    items = [
        (int(row["product_id"]), int(row["foreslått_antall"]))
        for _, row in curated.iterrows()
    ]
    ok = add_products(client, list_id, items)
    console.print(f"\n[green]✓[/green] La til {ok}/{len(items)} varer")
    console.print(
        f"\n[bold]Listen din:[/bold] https://oda.com/no/account/lists/details/{list_id}/"
    )


if __name__ == "__main__":
    main()
