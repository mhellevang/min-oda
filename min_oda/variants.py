"""Finn alternative produkter innenfor samme varetype.

Brukes på handleliste-siden for å la brukeren bytte mellom varianter
(samme varetype, ulike produkt-id-er) — f.eks. forskjellige melkemerker
eller sjokoladevarianter — og legge til en ekstra variant i tillegg til
den foreslåtte representanten.
"""

from __future__ import annotations

import pandas as pd

from .product_types import annotate


def variants_for_type(
    lines: pd.DataFrame,
    key: str,
    limit: int = 10,
    blocked: set[int] | frozenset[int] = frozenset(),
) -> pd.DataFrame:
    """Topp `limit` produkter innenfor varetype `key`, sortert etter
    distinkte ordrer (popularitet) synkende.

    Kolonner: product_id, product_name, category, n_orders. Blokkerte
    produkter filtreres bort. Tom DataFrame hvis ingen treff."""
    df = lines.dropna(subset=["product_id", "product_name", "category"]).copy()
    if df.empty:
        return pd.DataFrame(columns=["product_id", "product_name", "category", "n_orders"])
    df["product_id"] = df["product_id"].astype(int)
    df = annotate(df)
    df = df[df["varetype"] == key]
    if blocked:
        df = df[~df["product_id"].isin(blocked)]
    if df.empty:
        return pd.DataFrame(columns=["product_id", "product_name", "category", "n_orders"])
    # Dedupe på product_id — samme pid kan ha ulike staver over tid
    # ("Bleier" vs "bleier"), og vi vil ha én rad per faktisk produkt.
    # Velg navn + kategori fra siste kjøp.
    n_orders = (
        df.groupby("product_id")["order_id"].nunique().rename("n_orders")
    )
    latest = (
        df.sort_values("date" if "date" in df.columns else "order_id")
        .groupby("product_id")
        .tail(1)[["product_id", "product_name", "category"]]
    )
    return (
        latest.merge(n_orders, on="product_id")
        .sort_values("n_orders", ascending=False)
        .head(limit)
        .reset_index(drop=True)
    )
