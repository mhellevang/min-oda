"""Restock-forslag: hvilke faste varer er i ferd med å gå tom?

Aggregerer kjøp per varetype (brød, melk, ost, …) og beregner median-
intervall mellom kjøp. "Forfaller neste kjøp" = sist kjøpt + median-
intervall. Produkter med høy variasjon i intervall (CV) får lavere
score — de er ikke ekte faste varer.

Bruk `--by-product` hvis du vil drille ned til konkrete produkt-id-er
i stedet for varetyper (gir flere rader med færre datapunkter per).

CLI:
    uv run python -m min_oda.restock                       # neste 14 dager, per varetype
    uv run python -m min_oda.restock --horizon 7
    uv run python -m min_oda.restock --by-product          # drill ned til produkt-id
    uv run python -m min_oda.restock --min-buys 4 --since 2024
    uv run python -m min_oda.restock --all                 # også ikke-forfalte
"""

from __future__ import annotations

import argparse
import re

import pandas as pd
from rich.console import Console
from rich.table import Table

from .data_loader import load_both
from .product_types import product_type

console = Console()

# Hopp over forbruksvarer som vokses ut av — samme regel som build_list.py.
SIZE_CODED_RE = re.compile(
    r"\bstr\.?\s*\d|\d+\s*-\s*\d+\s*kg|\b\d+\s*mnd\b|\btrinn\s*\d",
    re.IGNORECASE,
)
SIZE_CODED_MAX_AGE_DAYS = 120

EXCLUDE_KEYWORDS = ["gavekort", "pant"]


def compute_cadence(
    lines: pd.DataFrame,
    min_buys: int = 3,
    since: int | None = None,
    today: pd.Timestamp | None = None,
    abandon_factor: float = 2.5,
    abandon_floor_days: int = 30,
    max_median_days: int = 90,
    by_type: bool = True,
    recency_events: int | None = 20,
) -> pd.DataFrame:
    """Returnerer én rad per produkt (eller varetype) med kadens-statistikk.

    Produkter med median-intervall over `max_median_days` droppes — det er
    sjeldne kjøp, ikke faste varer (og medianen er upålitelig med få
    datapunkter på lange intervaller). Produkter med kortere median
    droppes hvis siste kjøp er lenger siden enn
    `max(median * abandon_factor, abandon_floor_days)` — da har de blitt
    forlatt, ikke "snart tomt".

    Med `by_type=True` grupperes kjøpene per varetype (brød, melk, …) i
    stedet for per product_id, slik at substituerbare varianter teller
    mot samme behov.

    `recency_events` styrer hvor mange nylige hendelser som brukes til
    å estimere median-intervall og snitt-kvantitet. Stabilitets-sjekkene
    (n_buys, abandon, max_median) ser fortsatt hele historikken, men
    prediksjons-tallene vektes mot nyere adferd. Varetyper med færre enn
    `recency_events` hendelser totalt bruker alt de har. Sett til None
    for å bruke hele historikken som før.

    Kolonner: key, product_name, category, n_buys, first, last,
    days_since, median_days, cv, due_date, days_until_due,
    avg_qty_per_event, status.
    `key` er enten product_id (default) eller varetype-streng (by_type).
    """
    df = lines.dropna(subset=["product_id", "product_name", "date"]).copy()
    df["product_id"] = df["product_id"].astype(int)
    if since:
        df = df[df["date"].dt.year >= since]

    if by_type:
        df["_type"] = df.apply(
            lambda r: product_type(r["product_name"], r.get("category"), r["product_id"]),
            axis=1,
        )
        df = df.dropna(subset=["_type"])
        group_key = "_type"
    else:
        group_key = "product_id"

    # En "kjøpshendelse" per ordre — selv om samme produkt har flere linjer
    # eller quantity > 1, regnes det som ett kjøp. Når vi grupperer på
    # type, teller flere ulike produkter av samme type i én ordre også
    # som ett kjøp av typen. Quantity summeres innen samme ordre så vi
    # vet hvor mange enheter brukeren faktisk handler per gang.
    events = (
        df.groupby([group_key, "order_id"])
        .agg(
            product_name=("product_name", "last"),
            category=("category", "last"),
            date=("date", "min"),
            quantity=("quantity", "sum"),
        )
        .reset_index()
        .rename(columns={group_key: "key"})
    )

    today = today or pd.Timestamp.now(tz="UTC")
    rows: list[dict] = []
    for key, sub in events.groupby("key"):
        sub = sub.sort_values("date")
        if len(sub) < min_buys:
            continue
        dates = sub["date"].tolist()
        last = dates[-1]
        days_since = (today - last).days

        # Bruk de nyligste hendelsene til prediksjon (median + qty) når vi
        # har nok — eldre adferd reflekterer ikke nødvendigvis hva som
        # forbrukes nå (f.eks. en sommervakanse-pause eller en husstand
        # som har vokst). Faller tilbake til hele historikken når en
        # varetype har færre datapunkter enn vinduet.
        if recency_events is not None and len(sub) > recency_events:
            recent = sub.tail(recency_events)
        else:
            recent = sub
        recent_dates = recent["date"].tolist()
        intervals = [(b - a).days for a, b in zip(recent_dates, recent_dates[1:])]
        median = float(pd.Series(intervals).median())
        mean = float(pd.Series(intervals).mean())
        std = float(pd.Series(intervals).std(ddof=0))
        cv = (std / mean) if mean > 0 else float("inf")
        due_date = last + pd.Timedelta(days=int(round(median)))
        days_until_due = (due_date - today).days
        avg_qty_per_event = float(recent["quantity"].sum()) / len(recent)

        # Når vi grupperer på type, viser vi siste-kjøpt-navn som
        # representativt produkt, men `key` forblir typenavnet.
        last_name = str(sub.sort_values("date")["product_name"].iloc[-1])
        last_cat = sub.sort_values("date")["category"].iloc[-1]

        rows.append(
            {
                "key": key,
                "product_name": last_name,
                "category": str(last_cat) if pd.notna(last_cat) else "",
                "n_buys": len(sub),
                "first": dates[0],
                "last": last,
                "days_since": days_since,
                "median_days": median,
                "cv": cv,
                "due_date": due_date,
                "days_until_due": days_until_due,
                "avg_qty_per_event": avg_qty_per_event,
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    # Sjeldne kjøp er ikke restock-kandidater — drop produkter med median
    # over terskelen.
    out = out[out["median_days"] <= max_median_days]

    # Drop forlatte produkter — siste kjøp ligger lengre tilbake enn vi
    # kan forvente for et fortsatt aktivt produkt.
    abandon_limit = (out["median_days"] * abandon_factor).clip(lower=abandon_floor_days)
    out = out[out["days_since"] <= abandon_limit]

    # Filtrer ut size-kodede produkter som vokses ut av
    is_size_coded = out["product_name"].str.contains(SIZE_CODED_RE, na=False)
    stale_cutoff = today - pd.Timedelta(days=SIZE_CODED_MAX_AGE_DAYS)
    out = out[~(is_size_coded & (out["last"] < stale_cutoff))]

    # Drop pant/gavekort
    low = out["product_name"].str.lower()
    for kw in EXCLUDE_KEYWORDS:
        out = out[~low.str.contains(kw, na=False)]
        low = out["product_name"].str.lower()

    # Status
    def status(row: pd.Series) -> str:
        d = row["days_until_due"]
        if d < -row["median_days"] * 0.5:
            return "forfalt"
        if d < 0:
            return "akkurat nå"
        if d <= 7:
            return "snart"
        return "i rute"

    out["status"] = out.apply(status, axis=1)
    return out.sort_values("days_until_due").reset_index(drop=True)


STATUS_COLOR = {
    "forfalt": "red",
    "akkurat nå": "yellow",
    "snart": "cyan",
    "i rute": "green",
}


def render(cadence: pd.DataFrame, horizon: int, show_all: bool, top: int,
           by_type: bool = True) -> None:
    if cadence.empty:
        console.print("[yellow]Ikke nok data — øk historikken eller senk --min-buys.[/yellow]")
        return

    if show_all:
        view = cadence
        title = f"Restock — alle {len(cadence)} faste {'varetyper' if by_type else 'produkter'}"
    else:
        view = cadence[cadence["days_until_due"] <= horizon]
        title = f"Restock — forfaller innen {horizon} dager" + (
            " (per varetype)" if by_type else ""
        )

    if view.empty:
        console.print(f"[green]Ingen forfaller innen {horizon} dager.[/green]")
        return

    view = view.head(top)

    t = Table(title=title)
    if by_type:
        t.add_column("Varetype")
        t.add_column("Siste kjøp (eksempel)")
    else:
        t.add_column("Produkt")
        t.add_column("Kategori")
    t.add_column("N", justify="right")
    t.add_column("Sist kjøpt")
    t.add_column("Dager siden", justify="right")
    t.add_column("Snitt-intervall", justify="right")
    t.add_column("CV", justify="right")
    t.add_column("Forfaller", justify="right")
    t.add_column("Status")

    for _, r in view.iterrows():
        color = STATUS_COLOR.get(r["status"], "white")
        cv_str = f"{r['cv']:.2f}" if r["cv"] != float("inf") else "—"
        d = int(r["days_until_due"])
        due_str = f"i dag" if d == 0 else (f"{-d} d siden" if d < 0 else f"om {d} d")
        if by_type:
            first = str(r["key"]).capitalize()
            second = str(r["product_name"])[:45]
        else:
            first = str(r["product_name"])[:50]
            second = str(r["category"])[:18]
        t.add_row(
            first,
            second,
            str(int(r["n_buys"])),
            str(r["last"].date()),
            str(int(r["days_since"])),
            f"{int(round(r['median_days']))} d",
            cv_str,
            due_str,
            f"[{color}]{r['status']}[/{color}]",
        )
    console.print(t)

    if not show_all:
        n_over = int((cadence["status"] == "forfalt").sum())
        n_now = int((cadence["status"] == "akkurat nå").sum())
        n_soon = int((cadence["status"] == "snart").sum())
        unit = "varetyper" if by_type else "produkter"
        console.print(
            f"\n[dim]Totalt: {n_over} forfalt · {n_now} akkurat nå · "
            f"{n_soon} snart · {len(cadence)} faste {unit} analysert. "
            f"CV = variasjonskoeffisient (lavt = pålitelig kadens).[/dim]"
        )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--horizon", type=int, default=14,
                   help="Vis produkter som forfaller innen så mange dager")
    p.add_argument("--min-buys", type=int, default=3,
                   help="Minst så mange kjøp for å regnes som fast vare")
    p.add_argument("--max-median", type=int, default=90,
                   help="Maks median-intervall (dager) — over dette regnes "
                        "produktet som sjeldent og droppes")
    p.add_argument("--since", type=int, help="Begrens til år ≥ dette")
    p.add_argument("--top", type=int, default=40, help="Maks antall rader i tabellen")
    p.add_argument("--by-product", action="store_true",
                   help="Drill ned til konkrete produkt-id-er i stedet for "
                        "varetype-aggregering")
    p.add_argument("--all", action="store_true",
                   help="Vis alle faste varer, ikke bare de som forfaller")
    args = p.parse_args()

    by_type = not args.by_product

    _, lines = load_both()
    cadence = compute_cadence(
        lines,
        min_buys=args.min_buys,
        since=args.since,
        max_median_days=args.max_median,
        by_type=by_type,
    )
    render(cadence, horizon=args.horizon, show_all=args.all, top=args.top,
           by_type=by_type)


if __name__ == "__main__":
    main()
