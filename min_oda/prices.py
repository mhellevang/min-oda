"""Sist betalte enhetspris per produkt — brukt som ca.-pris på handlelista.

`unit_price` i `lines.csv` er prisen betalt i hver enkelt ordre
(`gross_amount / quantity`), ikke nåværende pris på oda.com. Samme produkt
har derfor mange priser over tid. For et prisestimat per produkt tar vi
prisen fra den nyeste ordren som inneholder produktet.

Estimatet kan være litt utdatert eller farget av en kampanjepris den dagen,
så UI merker det som ca.-pris.
"""

from __future__ import annotations

import pandas as pd


def latest_unit_prices(lines: pd.DataFrame) -> dict[int, float]:
    """Map produkt-id → sist betalte enhetspris.

    Bruker `date` (joined fra orders) til å finne nyeste ordre per produkt.
    Rader uten gyldig pris hoppes over. Returnerer {} for tom input.
    """
    if lines.empty or "unit_price" not in lines.columns:
        return {}
    df = lines.dropna(subset=["product_id", "unit_price"])
    if df.empty:
        return {}
    if "date" in df.columns:
        df = df.sort_values("date")
    df = df.drop_duplicates("product_id", keep="last")
    return {
        int(pid): float(price)
        for pid, price in zip(df["product_id"], df["unit_price"])
    }
