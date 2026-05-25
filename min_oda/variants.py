"""Finn alternative produkter innenfor samme varetype.

Brukes på handleliste-siden for å la brukeren bytte mellom varianter
(samme varetype, ulike produkt-id-er) — f.eks. forskjellige melkemerker
eller sjokoladevarianter — og legge til en ekstra variant i tillegg til
den foreslåtte representanten.
"""

from __future__ import annotations

import pandas as pd

from .product_types import product_type


def _classify(lines: pd.DataFrame) -> pd.DataFrame:
    """Returner kun rader med klassifisert varetype, med kolonnen `_type`.
    Beregner product_type på unike (pid, name, category)-kombinasjoner og
    joiner tilbake — sparer tid på store lines-tabeller."""
    df = lines.dropna(subset=["product_id", "product_name", "category"]).copy()
    if df.empty:
        return df
    df["product_id"] = df["product_id"].astype(int)
    keys = df.drop_duplicates(subset=["product_id", "product_name", "category"])[
        ["product_id", "product_name", "category"]
    ]
    keys = keys.assign(
        _type=keys.apply(
            lambda r: product_type(r["product_name"], r.get("category"), r["product_id"]),
            axis=1,
        )
    )
    return df.merge(keys, on=["product_id", "product_name", "category"], how="left")


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
    df = _classify(lines)
    if df.empty:
        return pd.DataFrame(columns=["product_id", "product_name", "category", "n_orders"])
    df = df[df["_type"] == key]
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
