"""Verdien fra siste kjøp, per produkt: pris og bilde.

Begge kolonnene i `lines.csv` er historiske, ikke nåværende: `unit_price`
er prisen betalt i hver enkelt ordre (`gross_amount / quantity`), og
`product_image` er bilde-URL-en Oda leverte den gangen. Samme produkt har
derfor mange verdier over tid, og vi tar den fra den nyeste ordren som
inneholder produktet.

Prisen kan være litt utdatert eller farget av en kampanjepris den dagen,
så UI-et merker den som ca.-pris.
"""

from __future__ import annotations

from typing import Callable

import pandas as pd


def siste_priser(lines: pd.DataFrame) -> dict[int, float]:
    """Map produkt-id → sist betalte enhetspris. {} for tom input."""
    return _siste_verdier(lines, "unit_price", float)


def siste_bilder(lines: pd.DataFrame) -> dict[int, str]:
    """Map produkt-id → bilde-URL fra nyeste ordre som har en. {} for tom
    input eller når kolonnen mangler (CSV bygget før bilder ble med)."""
    return _siste_verdier(
        lines, "product_image", str,
        gyldig=lambda kol: kol.astype(str).str.startswith("http"),
    )


def _siste_verdier(
    lines: pd.DataFrame,
    kolonne: str,
    konverter: Callable,
    gyldig: Callable[[pd.Series], pd.Series] | None = None,
) -> dict:
    """Verdien fra nyeste ordre per produkt, der kolonnen har en gyldig
    verdi. Bruker `date` (joined fra orders) når den finnes; uten den
    gjelder rekkefølgen i dataene."""
    if lines.empty or kolonne not in lines.columns:
        return {}
    df = lines.dropna(subset=["product_id", kolonne])
    if gyldig is not None and not df.empty:
        df = df[gyldig(df[kolonne])]
    if df.empty:
        return {}
    if "date" in df.columns:
        df = df.sort_values("date")
    df = df.drop_duplicates("product_id", keep="last")
    return {
        int(pid): konverter(verdi)
        for pid, verdi in zip(df["product_id"], df[kolonne])
    }
