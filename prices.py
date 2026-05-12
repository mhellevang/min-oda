"""Pris-analyse: personlig matprisindeks, per-produkt prisutvikling og MVA-mix.

Sammenligner valgfritt mot SSB KPI for matvarer (tabell 03013).

CLI:
    uv run prices.py
    uv run prices.py --top 30
    uv run prices.py --since 2020
    uv run prices.py --ssb           # Hent SSB KPI og sammenlign
"""

from __future__ import annotations

import argparse
from pathlib import Path

import httpx
import matplotlib.pyplot as plt
import pandas as pd
from rich.console import Console
from rich.table import Table

console = Console()
DATA_DIR = Path(__file__).parent / "data"
PLOTS_DIR = Path(__file__).parent / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

SSB_TABLE_URL = "https://data.ssb.no/api/v0/no/table/03013"


def load() -> pd.DataFrame:
    orders = pd.read_csv(DATA_DIR / "orders.csv", parse_dates=["date"])
    lines = pd.read_csv(DATA_DIR / "lines.csv")
    orders_idx = orders.set_index("order_number")["date"]
    lines["date"] = pd.to_datetime(lines["order_id"].map(orders_idx), utc=True)
    lines["unit_price"] = pd.to_numeric(lines["unit_price"], errors="coerce")
    lines["line_total"] = pd.to_numeric(lines["line_total"], errors="coerce")
    lines["quantity"] = pd.to_numeric(lines["quantity"], errors="coerce").fillna(1)
    lines = lines.dropna(subset=["date", "unit_price", "product_id"])
    # Fjern pant/retur (negative beløp) og rotete null-priser
    lines = lines[lines["unit_price"] > 0]
    lines["vat_pct"] = lines["vat_percentage"].str.rstrip("%").astype(float)
    lines["quarter"] = (
        lines["date"].dt.tz_convert("Europe/Oslo").dt.tz_localize(None).dt.to_period("Q")
    )
    lines["year"] = lines["date"].dt.year
    return lines


MIN_PRODUCTS_PER_QUARTER = 10
MAX_QUARTER_GAP = 4
MIN_COMMON_PRODUCTS = 5


def personal_index(
    lines: pd.DataFrame,
    since: int | None = None,
    min_products: int = MIN_PRODUCTS_PER_QUARTER,
    max_gap: int = MAX_QUARTER_GAP,
    min_common: int = MIN_COMMON_PRODUCTS,
) -> pd.Series:
    """Kjedet veid prisindeks. For hvert kvartalspar regnes prisendring kun på
    produkter som finnes i begge kvartaler. Kjeden brytes (og starter på nytt)
    hvis et kvartal har < `min_products` produkter, gapet til forrige kvartal
    er > `max_gap` kvartaler, eller overlappet er < `min_common` produkter.
    Lengste sammenhengende segment returneres — anker (=100) er det første
    kvartalet i det segmentet. Slik unngår vi å bygge indeks over flerårige
    hull i datagrunnlaget."""
    df = lines.copy()
    if since:
        df = df[df["year"] >= since]
    if df.empty:
        return pd.Series(dtype=float)

    df["spend"] = df["unit_price"] * df["quantity"]
    by = (
        df.groupby(["product_id", "quarter"])
        .agg(price=("unit_price", "mean"), spend=("spend", "sum"))
        .reset_index()
    )

    products_per_q = by.groupby("quarter")["product_id"].nunique()
    valid = products_per_q[products_per_q >= min_products].index
    by = by[by["quarter"].isin(valid)]

    quarters = sorted(by["quarter"].unique())
    if len(quarters) < 2:
        return pd.Series(dtype=float)

    segments: list[dict] = []
    current: dict = {}
    last_q = None
    last_data = None
    for q in quarters:
        cur_data = by[by["quarter"] == q].set_index("product_id")
        if last_q is None:
            current = {q: 100.0}
            last_q, last_data = q, cur_data
            continue
        gap = (q.year - last_q.year) * 4 + (q.quarter - last_q.quarter)
        common = last_data.index.intersection(cur_data.index)
        weights = last_data.loc[common, "spend"] if len(common) else None
        if gap > max_gap or len(common) < min_common or weights.sum() == 0:
            if len(current) >= 2:
                segments.append(current)
            current = {q: 100.0}
            last_q, last_data = q, cur_data
            continue
        ratios = cur_data.loc[common, "price"] / last_data.loc[common, "price"]
        step = (ratios * weights).sum() / weights.sum()
        current[q] = current[last_q] * step
        last_q, last_data = q, cur_data
    if len(current) >= 2:
        segments.append(current)

    if not segments:
        return pd.Series(dtype=float)

    best = max(segments, key=len)
    idx = pd.Series(best).sort_index()
    idx.name = "personlig_indeks"
    return idx


def per_product_evolution(lines: pd.DataFrame, top_n: int) -> pd.DataFrame:
    """For mest-kjøpte produkter: snittpris første og siste kvartal, total endring
    og CAGR (annualisert) så ulike tidsspenn blir sammenlignbare. Krever ≥3 kjøp
    og minst 4 kvartaler mellom første og siste."""
    by_product = lines.groupby("product_id").agg(
        product_name=("product_name", "last"),
        spend=("line_total", "sum"),
        n=("date", "count"),
    )
    by_product = by_product[by_product["n"] >= 3]

    rows: list[dict] = []
    for pid in by_product.sort_values("spend", ascending=False).index:
        sub = lines[lines["product_id"] == pid]
        first_q, last_q = sub["quarter"].min(), sub["quarter"].max()
        span_q = (last_q.year - first_q.year) * 4 + (last_q.quarter - first_q.quarter)
        if span_q < 4:
            continue
        first_price = sub[sub["quarter"] == first_q]["unit_price"].mean()
        last_price = sub[sub["quarter"] == last_q]["unit_price"].mean()
        years = span_q / 4
        rows.append(
            {
                "product": str(by_product.loc[pid, "product_name"])[:55],
                "n": int(by_product.loc[pid, "n"]),
                "from_q": str(first_q),
                "to_q": str(last_q),
                "years": years,
                "first": first_price,
                "last": last_price,
                "pct": (last_price / first_price - 1) * 100,
                "cagr": ((last_price / first_price) ** (1 / years) - 1) * 100,
                "spend": by_product.loc[pid, "spend"],
            }
        )
        if len(rows) >= top_n:
            break

    return pd.DataFrame(rows)


def vat_mix(lines: pd.DataFrame) -> pd.DataFrame:
    by = lines.groupby(["year", "vat_pct"])["line_total"].sum().unstack(fill_value=0)
    by["total"] = by.sum(axis=1)
    return by


def fetch_ssb_food_cpi() -> pd.Series:
    """Henter månedlig KPI 'Matvarer og alkoholfrie drikkevarer' (gruppe 01)."""
    body = {
        "query": [
            {"code": "Konsumgrp", "selection": {"filter": "item", "values": ["01"]}},
            {
                "code": "ContentsCode",
                "selection": {"filter": "item", "values": ["KpiIndMnd"]},
            },
        ],
        "response": {"format": "json-stat2"},
    }
    r = httpx.post(SSB_TABLE_URL, json=body, timeout=30.0)
    r.raise_for_status()
    data = r.json()
    tid = data["dimension"]["Tid"]["category"]["index"]
    values = data["value"]
    periods = sorted(tid.keys(), key=lambda k: tid[k])
    series = pd.Series(
        [values[tid[p]] for p in periods],
        index=[pd.Period(p.replace("M", "-"), freq="M") for p in periods],
        name="ssb_kpi_mat",
    )
    return series.dropna()


def plot_index(personal: pd.Series, ssb: pd.Series | None = None) -> None:
    fig, ax = plt.subplots(figsize=(13, 5))
    p_ts = personal.index.to_timestamp()
    ax.plot(p_ts, personal.values, marker="o", color="#2a6f97",
            label="Min Oda-kurv", linewidth=2)

    if ssb is not None:
        first_ts = personal.index[0].start_time
        last_ts = personal.index[-1].end_time
        ssb_ts = ssb.index.to_timestamp()
        mask = (ssb_ts >= first_ts) & (ssb_ts <= last_ts)
        ssb_window = ssb[mask]
        if not ssb_window.empty:
            ssb_norm = ssb_window / ssb_window.iloc[0] * 100
            ax.plot(ssb_norm.index.to_timestamp(), ssb_norm.values,
                    color="#d62828", label="SSB KPI matvarer", linewidth=2)

    ax.axhline(100, color="gray", linestyle=":", linewidth=1)
    ax.set_title("Personlig matprisindeks vs SSB KPI matvarer (start = 100)")
    ax.set_ylabel("Indeks")
    ax.grid(axis="y", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    out = PLOTS_DIR / "price_index.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    console.print(f"\n[green]→[/green] {out.relative_to(Path.cwd())}")


def render_index_table(idx: pd.Series) -> None:
    t = Table()
    t.add_column("Kvartal")
    t.add_column("Indeks (start=100)", justify="right")
    keep = list(range(0, len(idx), 4))
    if (len(idx) - 1) not in keep:
        keep.append(len(idx) - 1)
    for i in keep:
        t.add_row(str(idx.index[i]), f"{idx.iloc[i]:.1f}")
    console.print(t)

    first, last = idx.iloc[0], idx.iloc[-1]
    n_years = max(1, idx.index[-1].year - idx.index[0].year)
    cagr = ((last / first) ** (1 / n_years) - 1) * 100
    console.print(
        f"\n[bold]Total endring:[/bold] {(last/first - 1)*100:+.1f}% over "
        f"{n_years} år ([bold]{cagr:+.2f}%[/bold] per år, CAGR)"
    )


def render_evolution_table(evo: pd.DataFrame) -> None:
    t = Table()
    t.add_column("Produkt")
    t.add_column("N", justify="right")
    t.add_column("Periode")
    t.add_column("År", justify="right")
    t.add_column("Først", justify="right")
    t.add_column("Sist", justify="right")
    t.add_column("Total", justify="right")
    t.add_column("Per år", justify="right")
    for _, r in evo.sort_values("cagr", ascending=False).iterrows():
        color = "red" if r["cagr"] > 5 else ("green" if r["cagr"] < -2 else "white")
        t.add_row(
            r["product"],
            str(r["n"]),
            f"{r['from_q']}→{r['to_q']}",
            f"{r['years']:.1f}",
            f"{r['first']:.2f}",
            f"{r['last']:.2f}",
            f"{r['pct']:+.1f}%",
            f"[{color}]{r['cagr']:+.1f}%[/{color}]",
        )
    console.print(t)


def render_vat_table(vat: pd.DataFrame) -> None:
    rate_cols = sorted(c for c in vat.columns if c != "total")
    t = Table()
    t.add_column("År")
    t.add_column("Sum kr", justify="right")
    for c in rate_cols:
        t.add_column(f"{int(c)}% andel", justify="right")
    for y, row in vat.iterrows():
        cells = [str(int(y)), f"{row['total']:,.0f}"]
        for c in rate_cols:
            share = row[c] / row["total"] * 100 if row["total"] else 0
            cells.append(f"{share:.0f}%")
        t.add_row(*cells)
    console.print(t)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--top", type=int, default=25, help="Antall produkter i tabellen")
    p.add_argument("--since", type=int, help="Begrens til år ≥ dette")
    p.add_argument("--ssb", action="store_true", help="Hent SSB KPI og sammenlign")
    args = p.parse_args()

    lines = load()

    console.rule("[bold cyan]Personlig matprisindeks[/bold cyan]")
    idx = personal_index(lines, since=args.since)
    if idx.empty:
        console.print("[yellow]Ikke nok gjentatte kjøp til å beregne indeks.[/yellow]")
    else:
        render_index_table(idx)

    console.rule(f"[bold cyan]Topp {args.top} produkter — prisutvikling[/bold cyan]")
    evo = per_product_evolution(lines, top_n=args.top)
    if evo.empty:
        console.print("[yellow]For lite data per produkt.[/yellow]")
    else:
        render_evolution_table(evo)
        winners = evo.nsmallest(3, "cagr")
        losers = evo.nlargest(3, "cagr")
        console.print(
            f"\n[bold]Største nedganger (per år):[/bold] "
            + ", ".join(f"{r['product'][:30]} ({r['cagr']:+.1f}%/år)" for _, r in winners.iterrows())
        )
        console.print(
            f"[bold]Største oppganger (per år):[/bold] "
            + ", ".join(f"{r['product'][:30]} ({r['cagr']:+.1f}%/år)" for _, r in losers.iterrows())
        )

    console.rule("[bold cyan]MVA-mix per år[/bold cyan]")
    render_vat_table(vat_mix(lines))
    console.print(
        "[dim]15% = mat, 25% = non-food/snacks/drikke, 0% = pant.[/dim]"
    )

    ssb = None
    if args.ssb and not idx.empty:
        console.rule("[bold cyan]SSB-sammenligning[/bold cyan]")
        try:
            ssb = fetch_ssb_food_cpi()
        except httpx.HTTPError as e:
            console.print(f"[red]SSB-fetch feilet:[/red] {e}")
        else:
            first_ts = idx.index[0].start_time
            last_ts = idx.index[-1].end_time
            ssb_ts = ssb.index.to_timestamp()
            ssb_window = ssb[(ssb_ts >= first_ts) & (ssb_ts <= last_ts)]
            if ssb_window.empty:
                console.print("[yellow]Ingen overlapp i tidsperioden.[/yellow]")
            else:
                ssb_change = (ssb_window.iloc[-1] / ssb_window.iloc[0] - 1) * 100
                personal_change = (idx.iloc[-1] / idx.iloc[0] - 1) * 100
                diff = personal_change - ssb_change
                console.print(
                    f"[dim]Vindu: {idx.index[0]} → {idx.index[-1]} "
                    f"(samme periode for begge kilder).[/dim]"
                )
                t = Table()
                t.add_column("Kilde")
                t.add_column("Endring", justify="right")
                t.add_row("Min Oda-kurv", f"{personal_change:+.1f}%")
                t.add_row("SSB KPI matvarer (01)", f"{ssb_change:+.1f}%")
                t.add_row("Differanse", f"{diff:+.1f} pp")
                console.print(t)

    if not idx.empty:
        plot_index(idx, ssb)


if __name__ == "__main__":
    main()
