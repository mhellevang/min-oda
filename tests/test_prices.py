"""Tester for latest_unit_prices — sist betalte pris per produkt."""

from __future__ import annotations

import pandas as pd

from min_oda.prices import latest_unit_prices


def test_picks_newest_order_price():
    """Et produkt med flere kjøp får prisen fra nyeste ordre, ikke eldste."""
    df = pd.DataFrame({
        "product_id": [1, 1, 2],
        "unit_price": [10.0, 12.0, 5.0],
        "date": pd.to_datetime(
            ["2026-01-01", "2026-02-01", "2026-01-15"], utc=True
        ),
    })
    out = latest_unit_prices(df)
    assert out == {1: 12.0, 2: 5.0}


def test_skips_rows_without_price():
    df = pd.DataFrame({
        "product_id": [1, 2],
        "unit_price": [None, 7.5],
        "date": pd.to_datetime(["2026-01-01", "2026-01-02"], utc=True),
    })
    assert latest_unit_prices(df) == {2: 7.5}


def test_empty_input():
    assert latest_unit_prices(pd.DataFrame()) == {}


def test_works_without_date_column():
    """Uten date-kolonne faller vi tilbake på rekkefølgen i dataene."""
    df = pd.DataFrame({"product_id": [1, 1], "unit_price": [10.0, 20.0]})
    assert latest_unit_prices(df) == {1: 20.0}
