"""Sesongmønstre i ordrene — kontrollert for at handlefrekvensen
varierer over tid. Genererer også et månedlig spend-plot.

CLI:
    --cutoff YYYY-MM-DD       Dato for før/etter-sammenligning + graf-markør
    --label "Tekst"           Label for hendelsen (f.eks. "Flytting")
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from rich.console import Console
from rich.table import Table

from data_loader import load_both

console = Console()
PLOTS_DIR = Path(__file__).parent / "plots"
PLOTS_DIR.mkdir(exist_ok=True)

NB_MONTHS = ["", "jan", "feb", "mar", "apr", "mai", "jun",
             "jul", "aug", "sep", "okt", "nov", "des"]


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    orders, lines = load_both()
    orders["month_num"] = orders["date"].dt.month
    orders["year"] = orders["date"].dt.year
    return orders, lines


def section(title: str) -> None:
    console.rule(f"[bold cyan]{title}[/bold cyan]")


def true_seasonal_products(lines: pd.DataFrame) -> None:
    """Produkter med kort sesongvindu — kjøpt i ≤3 ulike måneder av året,
    minst 3 ganger totalt, og aldri samme måned 2 år på rad gir > spread."""
    section("Ekte sesongprodukter (kun visse måneder av året)")

    df = lines.dropna(subset=["product_name", "month_num"]).copy()
    rows = []
    for prod, sub in df.groupby("product_name"):
        total = len(sub)
        if total < 3:
            continue
        monthcount = sub["month_num"].value_counts().to_dict()
        if len(monthcount) > 3:
            continue
        low = str(prod).lower()
        if any(k in low for k in ["bleie", "morsmelk", "nan ", "nan,",
                                   "stellekluter", "våtservietter baby"]):
            continue
        peak_months = sorted(monthcount.keys())
        rows.append((prod, total, peak_months))

    rows.sort(key=lambda r: (-r[1], r[0]))

    t = Table()
    t.add_column("Produkt")
    t.add_column("Antall", justify="right")
    t.add_column("Måneder")
    for prod, n, mm in rows[:25]:
        t.add_row(str(prod)[:65], str(n), ",".join(NB_MONTHS[m] for m in mm))
    console.print(t)


def category_seasonality(lines: pd.DataFrame) -> None:
    """For hver kategori: hvilke måneder skiller seg ut?"""
    section("Kategori-sesong — månedene som skiller seg mest ut")

    cat_month = (
        lines.dropna(subset=["category", "month_num"])
        .groupby(["category", "month_num"])["line_total"]
        .sum()
        .unstack(fill_value=0)
    )
    orders_per_month = (
        lines.dropna(subset=["month_num", "order_id"])
        .groupby("month_num")["order_id"]
        .nunique()
    )
    norm = cat_month.div(orders_per_month, axis=1)
    rel = norm.div(norm.mean(axis=1), axis=0) - 1.0

    # Velg kategorier med tydelig variasjon (>=8 ordre totalt for å unngå støy)
    cat_volume = lines.groupby("category")["line_total"].sum()
    big_cats = cat_volume[cat_volume >= 1000].index
    rel_big = rel.loc[rel.index.intersection(big_cats)]
    interesting = rel_big.std(axis=1).sort_values(ascending=False).head(10).index

    t = Table()
    t.add_column("Kategori")
    t.add_column("Topp-måneder (over snittet)")
    t.add_column("Bunn-måneder (under snittet)")
    for cat in interesting:
        row = rel_big.loc[cat]
        top = row.nlargest(3)
        bot = row.nsmallest(3)
        top_s = ", ".join(f"{NB_MONTHS[int(m)]} {v*100:+.0f}%" for m, v in top.items() if v > 0.1)
        bot_s = ", ".join(f"{NB_MONTHS[int(m)]} {v*100:+.0f}%" for m, v in bot.items() if v < -0.1)
        t.add_row(str(cat)[:25], top_s or "—", bot_s or "—")
    console.print(t)


def july_gap(orders: pd.DataFrame) -> None:
    section("Sommerferie-gapet")

    by_month = orders.groupby("month_num").agg(
        ordrer=("order_number", "count"),
        sum_kr=("total", "sum"),
    )
    # Antall år hvor minst én ordre i den måneden
    years_per_month = (
        orders.dropna(subset=["year", "month_num"])
        .drop_duplicates(["year", "month_num"])
        .groupby("month_num")["year"]
        .count()
    )
    by_month["år_med_kjøp"] = years_per_month
    n_years = orders["year"].nunique()
    by_month["andel_år"] = (by_month["år_med_kjøp"] / n_years * 100).round(0)

    t = Table()
    t.add_column("Måned")
    t.add_column("Ordrer", justify="right")
    t.add_column("Sum kr", justify="right")
    t.add_column(f"År av {n_years}", justify="right")
    for m in range(1, 13):
        if m in by_month.index:
            r = by_month.loc[m]
            t.add_row(
                NB_MONTHS[m],
                str(int(r["ordrer"])),
                f"{r['sum_kr']:,.0f}",
                f"{int(r['år_med_kjøp'])} ({int(r['andel_år'])}%)",
            )
        else:
            t.add_row(NB_MONTHS[m], "0", "0", "0 (0%)")
    console.print(t)


def period_compare(
    orders: pd.DataFrame, lines: pd.DataFrame, cutoff: pd.Timestamp, label: str
) -> None:
    section(f"Før/etter {cutoff.date()} — månedlig spend ({label})")

    # Begrens "før" til samme lengde som "etter" så sammenligningen er rettferdig
    one_year_before = cutoff - pd.Timedelta(days=365)

    before = orders[(orders["date"] >= one_year_before) & (orders["date"] < cutoff)]
    after = orders[orders["date"] >= cutoff]

    def stats(df: pd.DataFrame, lbl: str) -> dict:
        if df.empty:
            return {}
        ms = df["date"].dt.tz_convert("Europe/Oslo").dt.tz_localize(None).dt.to_period("M")
        n_months = max(1, len(ms.unique()))
        return {
            "label": lbl,
            "ordrer": len(df),
            "måneder": n_months,
            "ordre_per_måned": len(df) / n_months,
            "kr_total": df["total"].sum(),
            "kr_per_måned": df["total"].sum() / n_months,
            "kr_per_ordre": df["total"].mean(),
        }

    a = stats(before, f"12 mnd før {cutoff.date()}")
    b = stats(after, f"Etter {cutoff.date()} ({label})")

    t = Table()
    t.add_column("Periode")
    t.add_column("Ordrer", justify="right")
    t.add_column("Mnd", justify="right")
    t.add_column("Ordre/mnd", justify="right")
    t.add_column("Kr/mnd", justify="right")
    t.add_column("Kr/ordre", justify="right")
    for s in (a, b):
        if not s:
            continue
        t.add_row(
            s["label"],
            str(s["ordrer"]),
            str(s["måneder"]),
            f"{s['ordre_per_måned']:.1f}",
            f"{s['kr_per_måned']:,.0f}",
            f"{s['kr_per_ordre']:,.0f}",
        )
    console.print(t)

    if a and b:
        delta_per_month = b["kr_per_måned"] - a["kr_per_måned"]
        pct = 100 * delta_per_month / a["kr_per_måned"]
        console.print(
            f"\n[bold]Effekt:[/bold] {delta_per_month:+,.0f} kr/mnd "
            f"({pct:+.0f} %) etter {cutoff.date()}.  "
            f"Årlig: ca. [bold]{delta_per_month*12:+,.0f} kr[/bold]."
        )

    lines_with_period = lines.dropna(subset=["date", "category"]).copy()
    lines_with_period["periode"] = "annet"
    mask_before = (lines_with_period["date"] >= one_year_before) & (
        lines_with_period["date"] < cutoff
    )
    mask_after = lines_with_period["date"] >= cutoff
    lines_with_period.loc[mask_before, "periode"] = "før"
    lines_with_period.loc[mask_after, "periode"] = "etter"

    by_cat = (
        lines_with_period[lines_with_period["periode"].isin(["før", "etter"])]
        .groupby(["category", "periode"])["line_total"]
        .sum()
        .unstack(fill_value=0)
    )

    if "før" in by_cat.columns and "etter" in by_cat.columns:
        # Per måned (12 før, ~7 etter)
        n_before = a["måneder"] if a else 12
        n_after = b["måneder"] if b else 7
        per_mnd = pd.DataFrame(
            {
                "før_per_mnd": by_cat["før"] / n_before,
                "etter_per_mnd": by_cat["etter"] / n_after,
            }
        )
        per_mnd["delta"] = per_mnd["etter_per_mnd"] - per_mnd["før_per_mnd"]
        per_mnd = per_mnd.sort_values("delta", ascending=False).head(10)

        console.print("\n[bold]Største kategori-økninger (kr/mnd):[/bold]")
        t = Table()
        t.add_column("Kategori")
        t.add_column("Før kr/mnd", justify="right")
        t.add_column("Etter kr/mnd", justify="right")
        t.add_column("Delta", justify="right")
        for cat, row in per_mnd.iterrows():
            t.add_row(
                str(cat)[:30],
                f"{row['før_per_mnd']:,.0f}",
                f"{row['etter_per_mnd']:,.0f}",
                f"+{row['delta']:,.0f}",
            )
        console.print(t)


def plot_spend_over_time(
    orders: pd.DataFrame,
    event_date: pd.Timestamp | None = None,
    event_label: str | None = None,
) -> None:
    """Lager monthly_spend.png. Hvis event_date er satt, tegnes en markør."""
    df = orders.dropna(subset=["date"]).copy()
    df["month"] = df["date"].dt.tz_convert("Europe/Oslo").dt.tz_localize(None).dt.to_period("M")
    monthly = df.groupby("month")["total"].sum()

    full_idx = pd.period_range(monthly.index.min(), monthly.index.max(), freq="M")
    monthly = monthly.reindex(full_idx, fill_value=0)

    fig, ax = plt.subplots(figsize=(13, 5))
    ax.bar(monthly.index.to_timestamp(), monthly.values, width=25, color="#2a6f97")

    if event_date is not None:
        marker = event_date.tz_localize(None) if event_date.tzinfo else event_date
        ax.axvline(marker, color="#d62828", linestyle="--", linewidth=1.5)
        ax.text(
            marker,
            monthly.max() * 0.95,
            f" {event_label or ''}",
            color="#d62828",
            va="top",
            fontsize=9,
        )

    yr_min = monthly.index.min().year
    yr_max = monthly.index.max().year
    ax.set_title(f"Månedlig forbruk på Oda ({yr_min}–{yr_max})")
    ax.set_ylabel("Sum kr")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    out = PLOTS_DIR / "monthly_spend.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    console.print(f"\n[green]→[/green] {out.relative_to(Path.cwd())}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--cutoff",
        help="Dato (YYYY-MM-DD) for før/etter-sammenligning og graf-markør",
    )
    p.add_argument(
        "--label",
        default="hendelse",
        help="Label som vises på grafen og i tabelloverskriften",
    )
    args = p.parse_args()

    orders, lines = load()
    true_seasonal_products(lines)
    category_seasonality(lines)
    july_gap(orders)

    cutoff = pd.Timestamp(args.cutoff, tz="UTC") if args.cutoff else None
    if cutoff is not None:
        period_compare(orders, lines, cutoff, args.label)
    plot_spend_over_time(orders, event_date=cutoff, event_label=args.label)


if __name__ == "__main__":
    main()
