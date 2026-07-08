"""Bygger en kuratert handleliste basert på faktiske handlevaner og oppretter
den på oda.com via /api/v1/product-lists/.

Bygger på `restock.compute_cadence(by_type=True)` — for hver varetype med
stabil kjøpsrytme velges sist kjøpte produkt som representant, og antall
beregnes fra listesyklus / median-intervall.

Bruk:
    uv run python -m min_oda.build_list                  # forhåndsvisning, oppretter ingenting
    uv run python -m min_oda.build_list --create         # oppretter listen på oda.com
    uv run python -m min_oda.build_list --title "X"      # egendefinert tittel
    uv run python -m min_oda.build_list --cycle 14       # 14-d syklus (default 7 d)
"""

from __future__ import annotations

import argparse
import math

import pandas as pd
from rich.console import Console
from rich.table import Table

from .blocklist import blocked_ids, blocked_types
from .data_loader import load_both
from .oda_client import LIST_URL, add_products, build_client, create_list
from .product_types import product_type
from .restock import compute_cadence

console = Console()

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

# Visse varetyper er kritiske uansett hvilken Oda-kategori produktet ender
# i. Bleier som dukker opp under "Faste, gode deals" er fortsatt bleier —
# uten denne overstyringen havner alle størrelses-subtyper i prio=999 og
# blir presset ut av top_n-cuten.
_TYPE_PRIORITY_OVERRIDE = {
    "bleier": "Bleier",
    "småbarn": "Småbarn 0-3",
    "babymat": "Babymat 6 mnd.",
    "morsmelkerstatning": "Småbarn 0-3",
}

def curate(
    lines: pd.DataFrame,
    list_cycle_days: int = 7,
    top_n: int = 40,
    max_per_category: int = 8,
    blocked: set[int] | frozenset[int] = frozenset(),
    blocked_types: set[str] | frozenset[str] = frozenset(),
    today: pd.Timestamp | None = None,
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

    `blocked` er et sett av produkt-id-er som skal utelukkes fra
    representant-valget — kadens-statistikken beholder fortsatt
    historikken, men en annen variant innen samme varetype kan ta over
    som forslag. Hvis alle varianter i en varetype er blokkert, faller
    varetypen ut av forslagene.

    `blocked_types` er et sett av varetype-nøkler (samme som `key`-kolonnen)
    som utelates helt fra forslagene — i motsetning til `blocked`, der en
    annen variant kan overta, forsvinner hele varetypen.

    `today` videresendes til `compute_cadence` slik at tester kan ankre
    abandon/forfall-tersklene til en fast dato. None betyr nå.
    """
    cadence = compute_cadence(lines, by_type=True, today=today)
    if cadence.empty:
        return cadence
    if blocked_types:
        cadence = cadence[~cadence["key"].isin(blocked_types)]
        if cadence.empty:
            return cadence

    # Finn representativt produkt per varetype: produktet med flest distinkte
    # ordrer (ikke det sist kjøpte — siste kan være en engangsvariant som
    # ikke representerer den faste rytmen).
    df = lines.dropna(subset=["product_id", "product_name", "category"]).copy()
    df["product_id"] = df["product_id"].astype(int)
    if blocked:
        df = df[~df["product_id"].isin(blocked)]
    if df.empty:
        # Ingen kandidater igjen etter blokk-filter — varetypen droppes via
        # det tomme rep-merge-et nedenfor. (apply på tom df returnerer
        # DataFrame, ikke Series, og krasjer kolonne-assignment i pandas 2.)
        return cadence.iloc[0:0]
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

    # Antall = forventet forbruk over sykluslengden:
    #   cycle × (snitt-kvantitet per besøk) / median-intervall.
    # Treårings-melk-kjøper med median=7 d og snitt 3 melk per ordre får
    # 6 melk på en 14-dagers liste, ikke 2.
    out["foreslått_antall"] = out.apply(
        lambda r: max(
            1,
            math.ceil(
                list_cycle_days * r["avg_qty_per_event"] / max(r["median_days"], 1)
            ),
        ),
        axis=1,
    )

    def prio(key: str, cat: str) -> int:
        base_type = str(key).split("-", 1)[0]
        promoted = _TYPE_PRIORITY_OVERRIDE.get(base_type)
        if promoted:
            try:
                return CATEGORY_PRIORITY.index(promoted)
            except ValueError:
                pass
        try:
            return CATEGORY_PRIORITY.index(cat)
        except ValueError:
            return 999

    out["_prio"] = out.apply(lambda r: prio(r["key"], r["category"]), axis=1)
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


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--create", action="store_true", help="Opprett listen på oda.com")
    p.add_argument("--title", default="Ukehandel — familien", help="Listetittel")
    p.add_argument(
        "--description",
        default="Faste varer basert på kjøpskadens per varetype",
    )
    p.add_argument("--cycle", type=int, default=7,
                   help="Listesyklus i dager — qty per vare = "
                        "ceil(syklus × snitt-per-besøk / median-intervall)")
    p.add_argument("--top", type=int, default=40, help="Maks antall varetyper")
    p.add_argument("--max-per-category", type=int, default=8,
                   help="Maks antall varetyper per Oda-kategori")
    args = p.parse_args()

    _, lines = load_both()
    curated = curate(
        lines,
        list_cycle_days=args.cycle,
        top_n=args.top,
        max_per_category=args.max_per_category,
        blocked=blocked_ids(),
        blocked_types=blocked_types(),
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
