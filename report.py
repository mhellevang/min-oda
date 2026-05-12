"""Samler de viktigste analysene i én selvstendig HTML-rapport.

Inkluderer nøkkeltall, topp-produkter/-kategorier siste 12 mnd, restock-
forslag, prisindeks (med SSB-sammenligning hvis tilgjengelig), sesong-
mønstre og basket-høydepunkter. Plot bakes inn som base64 så filen er
selvstendig og kan deles uten støtte-filer.

CLI:
    uv run report.py                    # → report.html
    uv run report.py --out custom.html
    uv run report.py --no-ssb           # hopp over SSB-fetch
"""

from __future__ import annotations

import argparse
import base64
import html
import io
from datetime import datetime
from pathlib import Path

import httpx
import matplotlib.pyplot as plt
import pandas as pd

from data_loader import load_both
from prices import (
    fetch_ssb_food_cpi,
    per_product_evolution,
    personal_index,
    plot_index,
    vat_mix,
)
from restock import compute_cadence

PLOTS_DIR = Path(__file__).parent / "plots"
PLOTS_DIR.mkdir(exist_ok=True)


# ---------- innlasting ---------------------------------------------------


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    orders, lines = load_both()
    lines = lines.dropna(subset=["date", "product_id"])
    lines["quarter"] = (
        lines["date"].dt.tz_convert("Europe/Oslo").dt.tz_localize(None).dt.to_period("Q")
    )
    return orders, lines


# ---------- plotting ----------------------------------------------------


def plot_monthly_spend(orders: pd.DataFrame) -> Path:
    """Månedlig forbruk — egen versjon her så report ikke avhenger av at
    seasonality.py har vært kjørt nylig."""
    df = orders.dropna(subset=["date"]).copy()
    df["month"] = (
        df["date"].dt.tz_convert("Europe/Oslo").dt.tz_localize(None).dt.to_period("M")
    )
    monthly = df.groupby("month")["total"].sum()
    full_idx = pd.period_range(monthly.index.min(), monthly.index.max(), freq="M")
    monthly = monthly.reindex(full_idx, fill_value=0)

    fig, ax = plt.subplots(figsize=(13, 4.5))
    ax.bar(monthly.index.to_timestamp(), monthly.values, width=25, color="#2a6f97")
    yr_min = monthly.index.min().year
    yr_max = monthly.index.max().year
    ax.set_title(f"Månedlig forbruk ({yr_min}–{yr_max})")
    ax.set_ylabel("Sum kr")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    out = PLOTS_DIR / "monthly_spend.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def img_data_uri(path: Path) -> str:
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{b64}"


# ---------- seksjoner ---------------------------------------------------


def kpis(orders: pd.DataFrame, lines: pd.DataFrame) -> dict:
    n_orders = len(orders)
    total = float(orders["total"].sum(skipna=True))
    avg = float(orders["total"].mean(skipna=True))
    date_min = orders["date"].min()
    date_max = orders["date"].max()
    n_days = (date_max - date_min).days if pd.notna(date_min) else 0
    freq = n_orders * 7 / n_days if n_days else 0
    unique_products = lines["product_name"].nunique()
    return {
        "n_orders": n_orders,
        "total_kr": total,
        "avg_kr": avg,
        "date_min": date_min,
        "date_max": date_max,
        "freq_per_week": freq,
        "n_unique_products": unique_products,
        "n_lines": len(lines),
    }


def top_products(lines: pd.DataFrame, months: int = 12, n: int = 15) -> pd.DataFrame:
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=months * 30)
    recent = lines[lines["date"] >= cutoff]
    return (
        recent.dropna(subset=["product_name"])
        .groupby("product_name")
        .agg(ganger=("order_id", "nunique"), sum_kr=("line_total", "sum"))
        .sort_values("ganger", ascending=False)
        .head(n)
        .reset_index()
    )


def top_categories(lines: pd.DataFrame, months: int = 12, n: int = 10) -> pd.DataFrame:
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=months * 30)
    recent = lines[lines["date"] >= cutoff]
    return (
        recent.dropna(subset=["category"])
        .groupby("category")
        .agg(linjer=("product_name", "count"), sum_kr=("line_total", "sum"))
        .sort_values("sum_kr", ascending=False)
        .head(n)
        .reset_index()
    )


def restock_view(lines: pd.DataFrame, horizon: int = 14, top: int = 20) -> pd.DataFrame:
    cadence = compute_cadence(lines, min_buys=3)
    if cadence.empty:
        return cadence
    soon = cadence[cadence["days_until_due"] <= horizon]
    return soon.head(top)


def seasonal_products(lines: pd.DataFrame, n: int = 10) -> list[tuple[str, int, list[int]]]:
    df = lines.dropna(subset=["product_name"]).copy()
    df["month_num"] = df["date"].dt.month
    rows: list[tuple[str, int, list[int]]] = []
    for prod, sub in df.groupby("product_name"):
        total = len(sub)
        if total < 3:
            continue
        monthcount = sub["month_num"].value_counts().to_dict()
        if len(monthcount) > 3:
            continue
        low = str(prod).lower()
        if any(k in low for k in ["bleie", "morsmelk", "nan "]):
            continue
        rows.append((str(prod), total, sorted(monthcount.keys())))
    rows.sort(key=lambda r: -r[1])
    return rows[:n]


# ---------- HTML --------------------------------------------------------


CSS = """
:root {
  --bg: #fafaf8;
  --card: #fff;
  --ink: #1a1a1a;
  --muted: #666;
  --rule: #e5e3df;
  --accent: #2a6f97;
  --warn: #d62828;
  --ok: #2a9d3f;
}
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  line-height: 1.5;
}
.wrap { max-width: 1100px; margin: 0 auto; padding: 32px 24px 64px; }
h1 { font-size: 28px; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 18px; margin: 0 0 14px; letter-spacing: -0.005em; }
.subtitle { color: var(--muted); margin: 0 0 28px; font-size: 14px; }
.kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  margin-bottom: 28px;
}
.kpi {
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: 8px;
  padding: 14px 16px;
}
.kpi .label { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
.kpi .value { font-size: 22px; font-weight: 600; margin-top: 2px; }
.kpi .sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
section {
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: 10px;
  padding: 20px 22px;
  margin-bottom: 20px;
}
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { padding: 7px 8px; text-align: left; border-bottom: 1px solid var(--rule); }
th { font-weight: 600; font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.bar-bg { background: #eee; border-radius: 3px; height: 8px; overflow: hidden; width: 120px; }
.bar { background: var(--accent); height: 100%; }
img.plot { width: 100%; height: auto; display: block; }
.status { font-size: 12px; padding: 2px 8px; border-radius: 999px; display: inline-block; }
.status-forfalt { background: #fde2e2; color: #b71c1c; }
.status-nå { background: #fff4d4; color: #8a6d00; }
.status-snart { background: #d4ecf7; color: #0a557a; }
footer { color: var(--muted); font-size: 12px; margin-top: 32px; text-align: center; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
@media (max-width: 720px) { .cols { grid-template-columns: 1fr; } }
"""


def esc(x) -> str:
    return html.escape(str(x))


def kpi_card(label: str, value: str, sub: str = "") -> str:
    sub_html = f'<div class="sub">{esc(sub)}</div>' if sub else ""
    return (
        f'<div class="kpi"><div class="label">{esc(label)}</div>'
        f'<div class="value">{esc(value)}</div>{sub_html}</div>'
    )


def render_kpis(k: dict) -> str:
    fmt_kr = lambda v: f"{v:,.0f} kr".replace(",", " ")
    period = ""
    if pd.notna(k["date_min"]) and pd.notna(k["date_max"]):
        period = f'{k["date_min"].date()} → {k["date_max"].date()}'
    cards = [
        kpi_card("Ordrer", str(k["n_orders"]), period),
        kpi_card("Totalt brukt", fmt_kr(k["total_kr"])),
        kpi_card("Snitt per ordre", fmt_kr(k["avg_kr"])),
        kpi_card("Frekvens", f'{k["freq_per_week"]:.2f} /uke'),
        kpi_card("Unike produkter", str(k["n_unique_products"])),
        kpi_card("Linjer", str(k["n_lines"])),
    ]
    return f'<div class="kpis">{"".join(cards)}</div>'


def render_top_products(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    max_g = df["ganger"].max()
    rows = []
    for _, r in df.iterrows():
        w = int(r["ganger"] / max_g * 100)
        rows.append(
            f'<tr><td>{esc(r["product_name"])}</td>'
            f'<td class="num">{int(r["ganger"])}</td>'
            f'<td><div class="bar-bg"><div class="bar" style="width:{w}%"></div></div></td>'
            f'<td class="num">{r["sum_kr"]:,.0f}</td></tr>'
        )
    return (
        "<section><h2>Topp produkter — siste 12 måneder</h2>"
        '<table><thead><tr><th>Produkt</th><th class="num">Ganger</th>'
        '<th></th><th class="num">Sum kr</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></section>'
    )


def render_categories(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    max_kr = df["sum_kr"].max()
    rows = []
    for _, r in df.iterrows():
        w = int(r["sum_kr"] / max_kr * 100)
        rows.append(
            f'<tr><td>{esc(r["category"])}</td>'
            f'<td class="num">{int(r["linjer"])}</td>'
            f'<td><div class="bar-bg"><div class="bar" style="width:{w}%"></div></div></td>'
            f'<td class="num">{r["sum_kr"]:,.0f}</td></tr>'
        )
    return (
        "<section><h2>Top kategorier — siste 12 måneder</h2>"
        '<table><thead><tr><th>Kategori</th><th class="num">Linjer</th>'
        '<th></th><th class="num">Sum kr</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></section>'
    )


def render_restock(view: pd.DataFrame, horizon: int) -> str:
    if view.empty:
        return (
            f'<section><h2>Restock — forfaller innen {horizon} dager</h2>'
            "<p>Ingen varetyper forfaller akkurat nå.</p></section>"
        )
    rows = []
    for _, r in view.iterrows():
        d = int(r["days_until_due"])
        due = "i dag" if d == 0 else (f"{-d} d siden" if d < 0 else f"om {d} d")
        slug = {"forfalt": "forfalt", "akkurat nå": "nå", "snart": "snart"}.get(r["status"], "snart")
        rows.append(
            f'<tr><td><strong>{esc(str(r["key"]).capitalize())}</strong></td>'
            f'<td>{esc(r["product_name"])}</td>'
            f'<td class="num">{int(r["n_buys"])}</td>'
            f'<td>{r["last"].date()}</td>'
            f'<td class="num">{int(round(r["median_days"]))} d</td>'
            f'<td class="num">{due}</td>'
            f'<td><span class="status status-{slug}">{esc(r["status"])}</span></td></tr>'
        )
    return (
        f'<section><h2>Restock — forfaller innen {horizon} dager</h2>'
        '<table><thead><tr><th>Varetype</th><th>Siste kjøp (eksempel)</th>'
        '<th class="num">N</th><th>Sist kjøpt</th><th class="num">Snitt</th>'
        '<th class="num">Forfaller</th><th>Status</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></section>'
    )


def render_price_section(
    lines: pd.DataFrame, plot_uri: str | None, evo: pd.DataFrame,
    personal_change: float | None, ssb_change: float | None, years: float | None,
) -> str:
    cards = ""
    if personal_change is not None and years:
        cagr = ((1 + personal_change / 100) ** (1 / years) - 1) * 100
        cards += kpi_card("Total endring (kurv)", f"{personal_change:+.1f}%", f"{cagr:+.2f}% / år")
    if ssb_change is not None:
        cards += kpi_card("SSB KPI matvarer", f"{ssb_change:+.1f}%", "samme periode")
    if personal_change is not None and ssb_change is not None:
        diff = personal_change - ssb_change
        sub = "din kurv steg mer" if diff > 0 else "din kurv steg mindre"
        cards += kpi_card("Differanse", f"{diff:+.1f} pp", sub)

    plot_html = f'<img class="plot" src="{plot_uri}" alt="Prisindeks">' if plot_uri else ""
    cards_html = f'<div class="kpis">{cards}</div>' if cards else ""

    evo_html = ""
    if not evo.empty:
        ups = evo.nlargest(5, "cagr")
        downs = evo.nsmallest(5, "cagr")
        def evo_row(r):
            return (
                f'<tr><td>{esc(r["product"])}</td>'
                f'<td class="num">{r["first"]:.2f}</td>'
                f'<td class="num">{r["last"]:.2f}</td>'
                f'<td class="num">{r["cagr"]:+.1f}%</td></tr>'
            )
        up_rows = "".join(evo_row(r) for _, r in ups.iterrows())
        down_rows = "".join(evo_row(r) for _, r in downs.iterrows())
        evo_html = (
            '<div class="cols">'
            f'<div><h2>Største oppganger (per år)</h2>'
            '<table><thead><tr><th>Produkt</th><th class="num">Først</th>'
            '<th class="num">Sist</th><th class="num">CAGR</th></tr></thead>'
            f'<tbody>{up_rows}</tbody></table></div>'
            f'<div><h2>Største nedganger (per år)</h2>'
            '<table><thead><tr><th>Produkt</th><th class="num">Først</th>'
            '<th class="num">Sist</th><th class="num">CAGR</th></tr></thead>'
            f'<tbody>{down_rows}</tbody></table></div>'
            "</div>"
        )

    return (
        "<section><h2>Prisutvikling</h2>"
        f"{cards_html}{plot_html}{evo_html}</section>"
    )


def render_monthly_section(plot_uri: str) -> str:
    return (
        '<section><h2>Månedlig forbruk</h2>'
        f'<img class="plot" src="{plot_uri}" alt="Månedlig forbruk"></section>'
    )


def render_seasonal(seasons: list[tuple[str, int, list[int]]]) -> str:
    if not seasons:
        return ""
    months_no = ["", "jan", "feb", "mar", "apr", "mai", "jun",
                 "jul", "aug", "sep", "okt", "nov", "des"]
    rows = "".join(
        f"<tr><td>{esc(p)}</td><td class='num'>{n}</td>"
        f"<td>{', '.join(months_no[m] for m in mm)}</td></tr>"
        for p, n, mm in seasons
    )
    return (
        "<section><h2>Sesongprodukter — kjøpt kun visse måneder</h2>"
        '<table><thead><tr><th>Produkt</th><th class="num">Ganger</th>'
        '<th>Måneder</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></section>"
    )


def build_html(parts: dict[str, str]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="no">
<head>
<meta charset="utf-8">
<title>Oda-rapport</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<h1>Oda-rapport</h1>
<p class="subtitle">Generert {now}</p>
{parts.get("kpis", "")}
{parts.get("monthly", "")}
{parts.get("restock", "")}
{parts.get("top_products", "")}
{parts.get("categories", "")}
{parts.get("prices", "")}
{parts.get("seasonal", "")}
<footer>Bygget fra lokale CSV-er — ingen data forlater maskinen.</footer>
</div>
</body>
</html>
"""


# ---------- main --------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="report.html", help="Filnavn for HTML-rapporten")
    p.add_argument("--horizon", type=int, default=14, help="Restock-horisont (dager)")
    p.add_argument("--no-ssb", action="store_true", help="Ikke hent SSB KPI")
    args = p.parse_args()

    orders, lines = load()

    monthly_path = plot_monthly_spend(orders)

    idx = personal_index(lines)
    ssb = None
    personal_change = ssb_change = years = None
    if not idx.empty:
        if not args.no_ssb:
            try:
                ssb = fetch_ssb_food_cpi()
            except (httpx.HTTPError, Exception) as e:
                print(f"SSB-fetch feilet: {e}. Fortsetter uten.")
                ssb = None
        plot_index(idx, ssb)  # skriver plots/price_index.png
        personal_change = float((idx.iloc[-1] / idx.iloc[0] - 1) * 100)
        years = (idx.index[-1].year - idx.index[0].year) + (
            (idx.index[-1].quarter - idx.index[0].quarter) / 4
        )
        if ssb is not None:
            first_ts = idx.index[0].start_time
            last_ts = idx.index[-1].end_time
            ssb_ts = ssb.index.to_timestamp()
            window = ssb[(ssb_ts >= first_ts) & (ssb_ts <= last_ts)]
            if not window.empty:
                ssb_change = float((window.iloc[-1] / window.iloc[0] - 1) * 100)

    evo = per_product_evolution(lines, top_n=30)

    parts = {
        "kpis": render_kpis(kpis(orders, lines)),
        "monthly": render_monthly_section(img_data_uri(monthly_path)),
        "restock": render_restock(restock_view(lines, horizon=args.horizon), args.horizon),
        "top_products": render_top_products(top_products(lines)),
        "categories": render_categories(top_categories(lines)),
        "prices": render_price_section(
            lines,
            img_data_uri(PLOTS_DIR / "price_index.png") if (PLOTS_DIR / "price_index.png").exists() else None,
            evo,
            personal_change,
            ssb_change,
            years,
        ),
        "seasonal": render_seasonal(seasonal_products(lines)),
    }

    out = Path(args.out)
    out.write_text(build_html(parts))
    size_kb = out.stat().st_size / 1024
    print(f"→ {out}  ({size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
