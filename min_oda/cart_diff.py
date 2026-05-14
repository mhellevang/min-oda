"""Sammenligner ideell handleliste (build_list) mot faktisk handlekurv på
oda.com og foreslår hva som mangler. Kan opprette listen direkte.

Sammenligningen skjer på varetype-nivå — har du allerede TINE Lettmelk i
kurven regnes "melk"-behovet som dekket selv om build_list foreslo et
annet merke. Det forhindrer falske mangler ved merkebytte.

Bruk:
    uv run python -m min_oda.cart_diff                   # forhåndsvisning av mangler
    uv run python -m min_oda.cart_diff --top-up          # ta også med varer med for lavt antall
    uv run python -m min_oda.cart_diff --cycle 7         # ukentlig syklus (default 14)
    uv run python -m min_oda.cart_diff --create          # opprett liste på oda.com
"""

from __future__ import annotations

import argparse

import pandas as pd
from rich.console import Console
from rich.table import Table

from .build_list import curate
from .data_loader import load_both
from .oda_client import LIST_URL, add_products, build_client, create_list
from .product_types import product_type

console = Console()

CART_URL = "https://oda.com/api/v1/cart/?group-by=categories"


def fetch_cart(client) -> pd.DataFrame:
    """Henter kurvens innhold og annoterer hver vare med varetype."""
    data = client.get(CART_URL).json()
    rows = []
    for group in data.get("groups", []):
        for item in group.get("items", []):
            p = item.get("product") or {}
            pid = p.get("id")
            if pid is None:
                continue
            rows.append({
                "product_id": int(pid),
                "product_name": p.get("full_name") or p.get("name") or "",
                "category": group.get("title") or "",
                "quantity": int(item.get("quantity") or 0),
            })
    if not rows:
        return pd.DataFrame(
            columns=["product_id", "product_name", "category", "quantity", "_type"]
        )
    df = pd.DataFrame(rows)
    df["_type"] = df.apply(
        lambda r: product_type(r["product_name"], r["category"], r["product_id"]),
        axis=1,
    )
    return df


def compute_diff(ideal: pd.DataFrame, cart: pd.DataFrame, top_up: bool) -> pd.DataFrame:
    """Rader fra ideal som ikke er dekket av kurven.

    Default: varetyper som ikke finnes i kurv i det hele tatt.
    top_up=True: også varetyper hvor sum-antall < foreslått antall.
    """
    if cart.empty:
        cart_qty: pd.Series = pd.Series(dtype=int)
    else:
        cart_qty = cart.dropna(subset=["_type"]).groupby("_type")["quantity"].sum()

    out = ideal.copy()
    out["i_kurv"] = out["key"].map(cart_qty).fillna(0).astype(int)
    out["mangler"] = (out["foreslått_antall"] - out["i_kurv"]).clip(lower=0)

    if top_up:
        return out[out["mangler"] > 0].reset_index(drop=True)
    return out[out["i_kurv"] == 0].reset_index(drop=True)


def show(missing: pd.DataFrame, cycle: int, top_up: bool, cart_count: int) -> None:
    if missing.empty:
        console.print(
            f"[green]Ingenting mangler.[/green] Kurven dekker hele "
            f"{cycle}-dagers listen ({cart_count} varer i kurv)."
        )
        return

    mode = "ikke fullt dekket" if top_up else "mangler helt"
    t = Table(
        title=f"Forslag — {len(missing)} varetyper {mode} "
              f"(syklus {cycle} d · {cart_count} i kurv)"
    )
    t.add_column("#", justify="right")
    t.add_column("Kategori")
    t.add_column("Varetype")
    t.add_column("Produkt (foreslag)")
    t.add_column("Forslag", justify="right")
    t.add_column("I kurv", justify="right")
    t.add_column("Mangler", justify="right")
    t.add_column("Kadens", justify="right")
    for i, row in missing.iterrows():
        median = int(round(row["median_days"]))
        t.add_row(
            str(i + 1),
            str(row["category"])[:22],
            str(row["key"]).capitalize()[:22],
            str(row["product_name"])[:42],
            str(int(row["foreslått_antall"])),
            str(int(row["i_kurv"])),
            str(int(row["mangler"])),
            f"hver {median}. d",
        )
    console.print(t)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cycle", type=int, default=14,
                   help="Listesyklus i dager (samme som build_list)")
    p.add_argument("--top", type=int, default=40,
                   help="Maks antall varetyper i ideell liste")
    p.add_argument("--top-up", action="store_true",
                   help="Inkluder varer som er i kurv, men med for lavt antall")
    p.add_argument("--create", action="store_true",
                   help="Opprett liste på oda.com med manglene")
    p.add_argument("--title", default="Resterende — ukehandel")
    p.add_argument("--description",
                   default="Forslag basert på diff mellom faste varer og handlekurv")
    args = p.parse_args()

    _, lines = load_both()
    ideal = curate(lines, list_cycle_days=args.cycle, top_n=args.top)
    if ideal.empty:
        console.print("[yellow]Ingen kandidater fra build_list — sjekk data.[/yellow]")
        return

    client = build_client()
    cart = fetch_cart(client)
    cart_count = int(cart["quantity"].sum()) if not cart.empty else 0

    missing = compute_diff(ideal, cart, top_up=args.top_up)
    show(missing, cycle=args.cycle, top_up=args.top_up, cart_count=cart_count)

    if not args.create:
        if not missing.empty:
            console.print(
                "\n[dim]Forhåndsvisning. Kjør med [bold]--create[/bold] for å lage liste.[/dim]"
            )
        return

    if missing.empty:
        return

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

    items = [
        (int(row["product_id"]), int(row["mangler"]))
        for _, row in missing.iterrows()
    ]
    ok = add_products(client, list_id, items)
    console.print(f"\n[green]✓[/green] La til {ok}/{len(items)} varer")
    console.print(
        f"\n[bold]Listen din:[/bold] https://oda.com/no/account/lists/details/{list_id}/"
    )


if __name__ == "__main__":
    main()
