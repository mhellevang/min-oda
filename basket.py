"""Basket-analyse: hvilke produkter havner ofte i samme ordre?

Beregner support, confidence og lift for produktpar. Filtrer til
produkter kjøpt i minst N ordre slik at sjeldne ting ikke gir falske
"perfekte" assosiasjoner.

CLI:
    uv run basket.py                              # topp lift + topp støtte
    uv run basket.py --min-orders 8 --top 25
    uv run basket.py --product "kokosmelk"        # hva følger med dette produktet?
"""

from __future__ import annotations

import argparse
from collections import Counter
from itertools import combinations
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

from data_loader import load_lines

console = Console()


def load() -> tuple[pd.DataFrame, dict[int, str]]:
    lines = load_lines()
    lines = lines.dropna(subset=["product_id", "order_id"])
    lines["product_id"] = lines["product_id"].astype(int)
    name_map = (
        lines.dropna(subset=["product_name"])
        .groupby("product_id")["product_name"]
        .agg(lambda s: s.mode().iat[0] if not s.mode().empty else s.iat[0])
        .to_dict()
    )
    return lines, name_map


def build_pairs(
    lines: pd.DataFrame, min_orders: int, min_pair: int
) -> tuple[pd.DataFrame, dict[int, int], int]:
    baskets = lines.groupby("order_id")["product_id"].apply(set).tolist()
    n_orders = len(baskets)

    prod_count: Counter[int] = Counter()
    for b in baskets:
        prod_count.update(b)

    frequent = {p for p, c in prod_count.items() if c >= min_orders}

    pair_count: Counter[tuple[int, int]] = Counter()
    for b in baskets:
        f = sorted(b & frequent)
        for a, c in combinations(f, 2):
            pair_count[(a, c)] += 1

    rows: list[dict] = []
    for (a, b), c in pair_count.items():
        if c < min_pair:
            continue
        pa, pb = prod_count[a], prod_count[b]
        rows.append(
            {
                "a": a,
                "b": b,
                "co": c,
                "support": c / n_orders,
                "conf_a_to_b": c / pa,
                "conf_b_to_a": c / pb,
                "lift": (c * n_orders) / (pa * pb),
            }
        )

    return pd.DataFrame(rows), dict(prod_count), n_orders


def name(name_map: dict[int, str], pid: int, max_len: int = 35) -> str:
    return str(name_map.get(pid, f"#{pid}"))[:max_len]


def section(title: str) -> None:
    console.rule(f"[bold cyan]{title}[/bold cyan]")


def show_top_lift(pairs: pd.DataFrame, name_map: dict, n: int) -> None:
    section(f"Topp {n} mest overraskende par (høyest lift)")
    top = pairs.sort_values("lift", ascending=False).head(n)
    t = Table()
    t.add_column("A")
    t.add_column("B")
    t.add_column("Co", justify="right")
    t.add_column("Lift", justify="right")
    t.add_column("Støtte", justify="right")
    t.add_column("A→B", justify="right")
    for _, r in top.iterrows():
        t.add_row(
            name(name_map, int(r["a"])),
            name(name_map, int(r["b"])),
            str(int(r["co"])),
            f"{r['lift']:.1f}×",
            f"{r['support']*100:.0f}%",
            f"{r['conf_a_to_b']*100:.0f}%",
        )
    console.print(t)


def show_top_support(pairs: pd.DataFrame, name_map: dict, n: int) -> None:
    section(f"Topp {n} mest vanlige par (høyest støtte)")
    top = pairs.sort_values("support", ascending=False).head(n)
    t = Table()
    t.add_column("A")
    t.add_column("B")
    t.add_column("Co", justify="right")
    t.add_column("Støtte", justify="right")
    t.add_column("Lift", justify="right")
    for _, r in top.iterrows():
        t.add_row(
            name(name_map, int(r["a"])),
            name(name_map, int(r["b"])),
            str(int(r["co"])),
            f"{r['support']*100:.0f}%",
            f"{r['lift']:.1f}×",
        )
    console.print(t)


def show_for_product(
    pairs: pd.DataFrame,
    name_map: dict,
    counts: dict[int, int],
    n_orders: int,
    query: str,
    n: int,
) -> None:
    matches = [pid for pid, nm in name_map.items() if query.lower() in nm.lower()]
    if not matches:
        console.print(f"[yellow]Fant ingen produkt med '{query}' i navnet.[/yellow]")
        return

    matches.sort(key=lambda p: counts.get(p, 0), reverse=True)
    pid = matches[0]
    base_count = counts.get(pid, 0)

    section(
        f"Følgesvenner til '{name_map[pid]}'  "
        f"(kjøpt i {base_count} ordrer av {n_orders})"
    )

    related = pairs[(pairs["a"] == pid) | (pairs["b"] == pid)].copy()
    if related.empty:
        console.print("[yellow]Ingen sterke assosiasjoner.[/yellow]")
        return

    related["other"] = related.apply(
        lambda r: int(r["b"]) if int(r["a"]) == pid else int(r["a"]), axis=1
    )
    related["conf_to_other"] = related.apply(
        lambda r: r["conf_a_to_b"] if int(r["a"]) == pid else r["conf_b_to_a"],
        axis=1,
    )

    top = related.sort_values("conf_to_other", ascending=False).head(n)
    t = Table()
    t.add_column("Følger med")
    t.add_column("Sammen i", justify="right")
    t.add_column("Når kjøpt", justify="right")
    t.add_column("Lift", justify="right")
    for _, r in top.iterrows():
        t.add_row(
            name(name_map, int(r["other"]), 45),
            f"{int(r['co'])}",
            f"{r['conf_to_other']*100:.0f}%",
            f"{r['lift']:.1f}×",
        )
    console.print(t)

    if len(matches) > 1:
        others = ", ".join(name_map[p][:30] for p in matches[1:5])
        console.print(f"\n[dim]Andre treff: {others}[/dim]")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--min-orders",
        type=int,
        default=6,
        help="Produktet må være kjøpt i minst så mange ordrer for å være med",
    )
    p.add_argument(
        "--min-pair",
        type=int,
        default=3,
        help="Et par må ha vært sammen i minst så mange ordrer",
    )
    p.add_argument("--top", type=int, default=20, help="Antall par i tabellene")
    p.add_argument("--product", help="Vis følgesvenner til ett spesifikt produkt")
    args = p.parse_args()

    lines, name_map = load()
    pairs, counts, n_orders = build_pairs(
        lines, min_orders=args.min_orders, min_pair=args.min_pair
    )

    if pairs.empty:
        console.print(
            f"[yellow]Ingen par passerte filteret "
            f"(min_orders={args.min_orders}, min_pair={args.min_pair}). "
            f"Prøv lavere terskler.[/yellow]"
        )
        return

    console.print(
        f"[dim]{n_orders} ordrer · "
        f"{sum(1 for c in counts.values() if c >= args.min_orders)} "
        f"produkter passerer ≥{args.min_orders} ordrer · "
        f"{len(pairs)} par etter filter[/dim]\n"
    )

    if args.product:
        show_for_product(pairs, name_map, counts, n_orders, args.product, args.top)
    else:
        show_top_lift(pairs, name_map, args.top)
        show_top_support(pairs, name_map, args.top)


if __name__ == "__main__":
    main()
