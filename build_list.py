"""Bygger en kuratert handleliste basert på faktiske handlevaner og oppretter
den på oda.com via /api/v1/product-lists/.

Bruk:
    uv run build_list.py                  # forhåndsvisning, oppretter ingenting
    uv run build_list.py --create         # oppretter listen på oda.com
    uv run build_list.py --title "X"      # egendefinert tittel
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import httpx
import pandas as pd
from rich.console import Console
from rich.table import Table

from fetch_orders import build_client
from data_loader import load_lines, load_orders

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

# Produkter vi IKKE vil ha på listen — for sjeldne/kuriøse, eller noe vi
# heller henter ferskt fra hyllen (krydder, etc.)
EXCLUDE_KEYWORDS = [
    "gavekort",
    "pant",
]

# Produkter med størrelse/alderstrinn vokses ut av — ikke stol på 12-mnd-frekvens.
# Treffer "Str. 5", "12-25 kg", "4 mnd", "trinn 2" osv.
SIZE_CODED_RE = re.compile(
    r"\bstr\.?\s*\d|\d+\s*-\s*\d+\s*kg|\b\d+\s*mnd\b|\btrinn\s*\d",
    re.IGNORECASE,
)
SIZE_CODED_MAX_AGE_DAYS = 120  # ~4 mnd


def load() -> pd.DataFrame:
    orders = load_orders()
    lines = load_lines(orders)
    return lines


def curate(lines: pd.DataFrame, top_n: int = 40) -> pd.DataFrame:
    """Velg ut produkter som inngår i 'rytmen': kjøpt mange ganger,
    fortsatt aktive (siste året), gir bredde over kategoriene."""
    df = lines.dropna(subset=["product_id", "product_name", "category"]).copy()
    df["product_id"] = df["product_id"].astype(int)

    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=365)
    recent = df[df["date"] >= cutoff]

    # Aggregér per produkt
    agg = (
        recent.groupby(["product_id", "product_name", "category"])
        .agg(
            ganger=("order_id", "nunique"),
            sum_qty=("quantity", "sum"),
            mean_qty=("quantity", "mean"),
            siste=("date", "max"),
        )
        .reset_index()
    )

    # Filtrer ut det vi ikke vil ha
    low = agg["product_name"].str.lower()
    for kw in EXCLUDE_KEYWORDS:
        agg = agg[~low.str.contains(kw, na=False)]
        low = agg["product_name"].str.lower()

    # Krev minst 2 separate ordre siste året for å regnes som 'fast'
    agg = agg[agg["ganger"] >= 2]

    # Størrelses-kodede produkter (bleier, melk-trinn, babymat 4/6/8 mnd osv.)
    # vokses ut av — krev at siste kjøp er innen ~4 mnd, ellers droppes de.
    is_size_coded = agg["product_name"].str.contains(SIZE_CODED_RE, na=False)
    fresh_cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=SIZE_CODED_MAX_AGE_DAYS)
    drop_stale = is_size_coded & (agg["siste"] < fresh_cutoff)
    if drop_stale.any():
        dropped = agg.loc[drop_stale, "product_name"].tolist()
        console.print(
            f"[dim]Droppet {len(dropped)} størrelses-kodet(e) produkter "
            f"(siste kjøp > {SIZE_CODED_MAX_AGE_DAYS} dager siden):[/dim]"
        )
        for name in dropped:
            console.print(f"  [dim]· {name}[/dim]")
    agg = agg[~drop_stale]

    # Velg topp-produkter, men sørg for variasjon: maks 4 per kategori
    agg = agg.sort_values(["ganger", "sum_qty"], ascending=False)
    keep = []
    cat_count: dict[str, int] = {}
    for _, row in agg.iterrows():
        cat = row["category"]
        if cat_count.get(cat, 0) >= 4:
            continue
        keep.append(row)
        cat_count[cat] = cat_count.get(cat, 0) + 1
        if len(keep) >= top_n:
            break

    out = pd.DataFrame(keep)
    # Foreslått antall = avrundet snitt-kvantum per ordre, minst 1
    out["foreslått_antall"] = out["mean_qty"].round().clip(lower=1).astype(int)

    # Sorter etter kategoriprioritet for visning
    def prio(cat: str) -> int:
        try:
            return CATEGORY_PRIORITY.index(cat)
        except ValueError:
            return 999

    out["_prio"] = out["category"].map(prio)
    out = out.sort_values(["_prio", "ganger"], ascending=[True, False]).drop(columns="_prio")
    return out.reset_index(drop=True)


def show(curated: pd.DataFrame) -> None:
    t = Table(title=f"Forslag til handleliste — {len(curated)} produkter")
    t.add_column("#", justify="right")
    t.add_column("Kategori")
    t.add_column("Produkt")
    t.add_column("Antall", justify="right")
    t.add_column("Ganger 12 mnd", justify="right")
    t.add_column("Sist kjøpt")
    for i, row in curated.iterrows():
        t.add_row(
            str(i + 1),
            str(row["category"])[:22],
            str(row["product_name"])[:55],
            str(row["foreslått_antall"]),
            str(int(row["ganger"])),
            str(row["siste"].date()) if pd.notna(row["siste"]) else "—",
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
        default="Faste varer basert på handlemønsteret siste 12 mnd",
    )
    p.add_argument("--top", type=int, default=40, help="Maks antall produkter")
    args = p.parse_args()

    lines = load()
    curated = curate(lines, top_n=args.top)
    show(curated)

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
