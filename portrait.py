"""Hva sier matvarene om husstanden? Eksplorativ analyse."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

from data_loader import load_both

console = Console()


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    orders, lines = load_both()
    return orders, lines


def kw(s: pd.Series, *patterns: str) -> pd.Series:
    rx = "|".join(patterns)
    return s.fillna("").str.contains(rx, case=False, regex=True)


def section(title: str) -> None:
    console.rule(f"[bold cyan]{title}[/bold cyan]")


def life_events(lines: pd.DataFrame) -> None:
    section("Livshendelser i handlevogna")

    baby_kw = r"bleier|stellekluter|våtservietter\s+baby|morsmelk|nan\s+1|nan\s+pro|barnegrøt|tåteflaske"
    baby = lines[kw(lines["product_name"], baby_kw)].dropna(subset=["date"])
    if baby.empty:
        return

    first = baby["date"].min()
    last = baby["date"].max()
    spent = baby["line_total"].sum()
    console.print(
        f"[bold]Baby/småbarn:[/bold] første kjøp "
        f"[bold]{pd.to_datetime(first).date()}[/bold], "
        f"siste {pd.to_datetime(last).date()}.  "
        f"{len(baby)} linjer, {spent:,.0f} kr."
    )

    bleier = lines[kw(lines["product_name"], r"bleie")].dropna(subset=["date"]).copy()
    bleier["str"] = bleier["product_name"].str.extract(r"[Ss]tr\.?\s*(\d)")
    if bleier["str"].notna().any():
        ts = (
            bleier.dropna(subset=["str"])
            .assign(month=lambda x: x["date"].dt.tz_convert("Europe/Oslo").dt.tz_localize(None).dt.to_period("M"))
            .groupby(["month", "str"])
            .size()
            .unstack(fill_value=0)
            .sort_index()
        )
        console.print(
            "\n[dim]Bleiestørrelse per måned. Parallelle små+store størrelser "
            "= flere barn samtidig:[/dim]"
        )
        t = Table()
        t.add_column("Måned")
        for c in ts.columns:
            t.add_column(f"Str {c}", justify="right")
        for m, row in ts.tail(14).iterrows():
            t.add_row(str(m), *[str(int(v)) if v else "·" for v in row])
        console.print(t)

    formula = lines[kw(lines["product_name"], r"nan\s+1|nan\s+pro|morsmelk")].dropna(
        subset=["date"]
    )
    if not formula.empty:
        formula_kr = formula["line_total"].sum()
        console.print(
            f"\nMorsmelkserstatning: [bold]{len(formula)}[/bold] linjer, "
            f"{int(formula['quantity'].sum())} enheter, "
            f"{formula_kr:,.0f} kr."
        )


def cuisine(lines: pd.DataFrame) -> None:
    section("Matkultur — hvor i verden spiser dere?")

    cuisines = {
        "Norsk tradisjon": r"jarlsberg|gilde|tine|kavli|brunost|fiskekake|kjøttkake|leverpostei|nugatti|prim|geitost|stange",
        "Italiensk": r"pasta|spagetti|pesto|parmesan|mozzarella|pizza|tomatpuré|risotto|prosciutto",
        "Meksikansk/Tex-Mex": r"tortilla|salsa|taco|nachos|guacamole|jalape|fajita|chili",
        "Asiatisk": r"soya|nudler|ramen|wok|sesam|kokosmelk|curry|sushi|tofu|ingefær",
        "Indisk": r"naan|tandoori|garam|tikka|masala|paneer|chutney",
        "Frokost-Norge": r"havre|müsli|cornflakes|yoghurt|juice|appelsinjuice",
    }
    rows = []
    total_spend = lines["line_total"].sum()
    for name, pat in cuisines.items():
        m = lines[kw(lines["product_name"], pat)]
        rows.append((name, len(m), m["line_total"].sum(), 100 * m["line_total"].sum() / total_spend))
    rows.sort(key=lambda r: -r[2])
    t = Table()
    t.add_column("Kjøkken")
    t.add_column("Linjer", justify="right")
    t.add_column("Sum kr", justify="right")
    t.add_column("Andel", justify="right")
    for name, n, s, p in rows:
        t.add_row(name, str(n), f"{s:,.0f}", f"{p:.1f} %")
    console.print(t)


def price_consciousness(lines: pd.DataFrame) -> None:
    section("Prisbevissthet")

    cheap_brands = r"vår laveste pris|first\s*price|eldorado|x-tra|coop\s*x"
    premium = r"jacobs|jamie oliver|stange\s|prior\s|tine\s|gilde\s"
    cheap = lines[kw(lines["product_name"], cheap_brands)]
    prem = lines[kw(lines["product_name"], premium)]
    total = lines["line_total"].sum()
    console.print(
        f"Lavpris-merker: [bold]{len(cheap)}[/bold] linjer, "
        f"{cheap['line_total'].sum():,.0f} kr ({100 * cheap['line_total'].sum() / total:.1f} %)"
    )
    console.print(
        f"Premium/merkevarer: [bold]{len(prem)}[/bold] linjer, "
        f"{prem['line_total'].sum():,.0f} kr ({100 * prem['line_total'].sum() / total:.1f} %)"
    )

    # Økologisk?
    eco = lines[kw(lines["product_name"], r"økologisk|øko\s")]
    console.print(
        f"Økologisk: [bold]{len(eco)}[/bold] linjer "
        f"({100 * len(eco) / len(lines):.1f} %)"
    )


def cooking_style(lines: pd.DataFrame) -> None:
    section("Lager dere mat eller varmer dere mat?")

    raw = r"agurk|tomat|paprika|løk|hvitløk|gulrot|potet|kjøttdeig|karbonadedeig|kylling|laks|torsk|egg\s|mel\s|ris\s|pasta\s"
    convenience = r"ferdig|grandiosa|fjordland|toro\s|knorr|stabbur|frosset.*middag|pizza"

    raw_m = lines[kw(lines["product_name"], raw)]
    conv_m = lines[kw(lines["product_name"], convenience)]
    total = lines["line_total"].sum()
    console.print(
        f"Råvarer: [bold]{len(raw_m)}[/bold] linjer, "
        f"{raw_m['line_total'].sum():,.0f} kr ({100 * raw_m['line_total'].sum() / total:.1f} %)"
    )
    console.print(
        f"Ferdigmat: [bold]{len(conv_m)}[/bold] linjer, "
        f"{conv_m['line_total'].sum():,.0f} kr ({100 * conv_m['line_total'].sum() / total:.1f} %)"
    )


def health_signals(lines: pd.DataFrame) -> None:
    section("Helsesignaler")

    veg = lines[lines["category"].fillna("") == "Frukt og grønt"]
    meat = lines[lines["category"].fillna("").isin(["Kylling og kjøtt", "Grill"])]
    fish = lines[lines["category"].fillna("") == "Fisk og sjømat"]
    sweets = lines[
        lines["category"]
        .fillna("")
        .isin(["Sjokolade, snacks og godteri", "Iskrem, dessert og kjeks"])
    ]
    drinks = lines[lines["category"].fillna("") == "Drikke"]

    total = lines["line_total"].sum()

    def show(name: str, sub: pd.DataFrame) -> None:
        s = sub["line_total"].sum()
        console.print(
            f"  {name:<25} {len(sub):>5} linjer  {s:>9,.0f} kr  ({100 * s / total:>4.1f} %)"
        )

    show("Frukt og grønt", veg)
    show("Kjøtt", meat)
    show("Fisk", fish)
    show("Søtt", sweets)
    show("Drikke", drinks)

    if not fish.empty:
        ratio = meat["line_total"].sum() / fish["line_total"].sum()
        console.print(f"\nKjøtt:fisk-forhold: [bold]{ratio:.1f}:1[/bold]")


# Mønstre for drikkevarer. Splittes per gruppe så alkoholfri/alkoholholdig ikke
# blandes, og brett/bokser fanges (ikke bare 1,5 l-flasker).
BEVERAGE_PATTERNS: dict[str, str] = {
    "Brus":
        r"pepsi|coca[-\s]?cola|coke\b|solo\b|farris|julebrus|sprite|fanta|7up|urge|battery|red\s*bull",
    "Øl (alkoholholdig)":
        r"peroni|frydenlund(?!.*alkoholfri)|schous\s+pilsner|sol\s+flaske|nøisom|"
        r"hansa(?!.*alkoholfri)|aass(?!.*alkoholfri)|ringnes\s+pils|tuborg|"
        r"carlsberg(?!.*alkoholfri)|heineken(?!.*alkoholfri)|mack\s+pils|"
        r"corona\s+extra|stella\s+artois|grans|nøgne|pale\s+ale|\bipa\b(?!.*alkoholfri)",
    "Øl (alkoholfri)":
        r"clausthaler|munkholm|no\s+worries|alkoholfri",
    "Juice/smoothie":
        r"appelsinjuice|eplejuice|tranebær|smoothie|froosh|juice\b|tropicana",
    "Kaffe":
        r"kaffe|kaffebønner|espresso|filtermalt|kaffekapsler|kapsler",
    "Vann":
        r"snåsavann|imsdal|farris\s+naturell|kildevann|mineralvann",
}


def beverages(lines: pd.DataFrame) -> None:
    section("Drikkevaner — brus, øl, kaffe")

    # Bruker hele datasettet (ikke bare Drikke-kategorien) siden noen produkter
    # er feilkategorisert (f.eks. Pepperoni i Pålegg fanges av "pepsi"-substring,
    # men ikke av regex over).
    rows = []
    seen_idx: set[int] = set()
    for label, pat in BEVERAGE_PATTERNS.items():
        m = lines[kw(lines["product_name"], pat)]
        # Unngå dobbelttelling mellom alkoholfri/alkoholholdig
        if "alkoholfri" in label.lower():
            seen_idx.update(m.index)
        elif "alkoholholdig" in label.lower():
            m = m[~m.index.isin(seen_idx)]
        rows.append(
            (
                label,
                len(m),
                int(m["quantity"].sum()),
                m["line_total"].sum(),
            )
        )

    rows.sort(key=lambda r: -r[3])
    t = Table()
    t.add_column("Type")
    t.add_column("Linjer", justify="right")
    t.add_column("Enheter", justify="right")
    t.add_column("Sum kr", justify="right")
    for label, n, units, kr in rows:
        t.add_row(label, str(n), str(units), f"{kr:,.0f}")
    console.print(t)

    # Vis topp i hver gruppe for transparens
    for label, pat in BEVERAGE_PATTERNS.items():
        m = lines[kw(lines["product_name"], pat)]
        if "alkoholholdig" in label.lower():
            af = lines[kw(lines["product_name"], BEVERAGE_PATTERNS["Øl (alkoholfri)"])]
            m = m[~m.index.isin(af.index)]
        if m.empty:
            continue
        top = (
            m.groupby("product_name")
            .agg(g=("quantity", "count"), e=("quantity", "sum"), kr=("line_total", "sum"))
            .sort_values("kr", ascending=False)
            .head(5)
        )
        console.print(f"\n[dim]{label} — top 5:[/dim]")
        for name, row in top.iterrows():
            console.print(
                f"  {int(row['g']):>3}× {int(row['e']):>3}stk  "
                f"{row['kr']:>6,.0f} kr  {str(name)[:65]}"
            )


def staples(orders: pd.DataFrame, lines: pd.DataFrame) -> None:
    section("Husholdningens DNA — varer dere kjøper i mer enn halvparten av ordrene")

    n_orders = len(orders)
    threshold = n_orders * 0.4
    counts = (
        lines.dropna(subset=["product_name"])
        .groupby("product_name")["order_id"]
        .nunique()
        .sort_values(ascending=False)
    )
    staples = counts[counts >= threshold]
    console.print(
        f"Av {n_orders} ordrer, disse går igjen i ≥{int(threshold)} av dem:\n"
    )
    for name, c in staples.head(20).items():
        console.print(f"  {c:>3}/{n_orders}  {name}")


def main() -> None:
    orders, lines = load()
    life_events(lines)
    cuisine(lines)
    price_consciousness(lines)
    cooking_style(lines)
    health_signals(lines)
    beverages(lines)
    staples(orders, lines)


if __name__ == "__main__":
    main()
