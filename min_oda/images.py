"""Produktbilde per produkt — brukt som miniatyrbilde på handlelista.

`product_image` i `lines.csv` er bilde-URL-en Oda leverte i ordredetaljene
(images.oda.com, signert URL). Samme produkt kan ha fått nytt bilde over tid,
så vi tar URL-en fra den nyeste ordren som inneholder produktet.
"""

from __future__ import annotations

import pandas as pd


def latest_product_images(lines: pd.DataFrame) -> dict[int, str]:
    """Map produkt-id → bilde-URL fra nyeste ordre.

    Bruker `date` (joined fra orders) til å finne nyeste ordre per produkt.
    Rader uten bilde hoppes over. Returnerer {} for tom input eller når
    kolonnen mangler (CSV bygget før bilder ble med).
    """
    if lines.empty or "product_image" not in lines.columns:
        return {}
    df = lines.dropna(subset=["product_id", "product_image"])
    df = df[df["product_image"].astype(str).str.startswith("http")]
    if df.empty:
        return {}
    if "date" in df.columns:
        df = df.sort_values("date")
    df = df.drop_duplicates("product_id", keep="last")
    return {
        int(pid): str(url)
        for pid, url in zip(df["product_id"], df["product_image"])
    }
