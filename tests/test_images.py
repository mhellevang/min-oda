"""Tester for latest_product_images — bilde-URL per produkt."""

from __future__ import annotations

import pandas as pd

from min_oda.images import latest_product_images


def test_picks_newest_order_image():
    """Et produkt med flere kjøp får bildet fra nyeste ordre."""
    df = pd.DataFrame({
        "product_id": [1, 1, 2],
        "product_image": [
            "https://images.oda.com/gammel.jpg",
            "https://images.oda.com/ny.jpg",
            "https://images.oda.com/annen.jpg",
        ],
        "date": pd.to_datetime(
            ["2026-01-01", "2026-02-01", "2026-01-15"], utc=True
        ),
    })
    out = latest_product_images(df)
    assert out == {
        1: "https://images.oda.com/ny.jpg",
        2: "https://images.oda.com/annen.jpg",
    }


def test_skips_rows_without_image():
    df = pd.DataFrame({
        "product_id": [1, 2],
        "product_image": [None, "https://images.oda.com/x.jpg"],
        "date": pd.to_datetime(["2026-01-01", "2026-01-02"], utc=True),
    })
    assert latest_product_images(df) == {2: "https://images.oda.com/x.jpg"}


def test_skips_non_url_values():
    """NaN som ble til strengen 'nan' i CSV-runden skal ikke slippe gjennom."""
    df = pd.DataFrame({
        "product_id": [1, 2],
        "product_image": ["nan", "https://images.oda.com/x.jpg"],
    })
    assert latest_product_images(df) == {2: "https://images.oda.com/x.jpg"}


def test_empty_input():
    assert latest_product_images(pd.DataFrame()) == {}


def test_missing_column():
    """CSV bygget før bilder ble med skal gi tomt kart, ikke krasj."""
    df = pd.DataFrame({"product_id": [1], "product_name": ["Melk"]})
    assert latest_product_images(df) == {}
