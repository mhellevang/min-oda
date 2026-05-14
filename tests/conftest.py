"""Fellesfixtures for unit-testene.

Synthetic lines-DataFrame ankret til en fast `today` slik at terskler
(abandon, max_median, status) gir deterministiske resultater. Hver
fixture lager nettopp så mye data som en test trenger.
"""

from __future__ import annotations

import pandas as pd
import pytest

TODAY = pd.Timestamp("2026-05-14", tz="UTC")


def _line(
    product_id: int,
    product_name: str,
    category: str,
    order_id: str,
    days_ago: int,
    quantity: int = 1,
) -> dict:
    return {
        "product_id": product_id,
        "product_name": product_name,
        "category": category,
        "order_id": order_id,
        "date": TODAY - pd.Timedelta(days=days_ago),
        "quantity": quantity,
        "line_total": 25.0 * quantity,
    }


@pytest.fixture
def today() -> pd.Timestamp:
    return TODAY


@pytest.fixture
def lines_milk_and_bread() -> pd.DataFrame:
    """To staples med stabil ukentlig kadens. Brukes som "alt fungerer"-baseline."""
    rows = []
    for i, d in enumerate([28, 21, 14, 7]):
        rows.append(_line(1, "TINE Lettmelk 1 L", "Meieri", f"o{i}", d))
    for i, d in enumerate([25, 20, 15, 10, 5]):
        rows.append(_line(2, "Korn Bakeri Solsikkebrød", "Bakeri", f"b{i}", d))
    return pd.DataFrame(rows)


@pytest.fixture
def lines_abandoned() -> pd.DataFrame:
    """Et produkt som ble kjøpt fast, så ble forlatt for over 6 mnd siden."""
    rows = []
    for i, d in enumerate([240, 230, 220, 210]):
        rows.append(_line(3, "Gilde Salami", "Pålegg", f"a{i}", d))
    return pd.DataFrame(rows)


@pytest.fixture
def lines_rare() -> pd.DataFrame:
    """Et produkt med median-intervall over 90 dager (sjeldne kjøp)."""
    rows = []
    for i, d in enumerate([300, 200, 100, 0]):
        rows.append(_line(4, "Aluminiumsfolie 30 m", "Husholdning", f"r{i}", d))
    return pd.DataFrame(rows)


@pytest.fixture
def lines_substitution() -> pd.DataFrame:
    """To ulike merker av samme varetype (melk). by_type=True skal slå dem sammen."""
    rows = []
    for i, d in enumerate([21, 14, 7]):
        rows.append(_line(10, "TINE Lettmelk 1 L", "Meieri", f"s{i}", d))
    for i, d in enumerate([28, 17, 3]):
        rows.append(_line(11, "Q-Meieriene Lettmelk 1 L", "Meieri", f"q{i}", d))
    return pd.DataFrame(rows)


@pytest.fixture
def lines_size_coded() -> pd.DataFrame:
    """Bleier med str.-kode, sist kjøpt for 5 mnd siden (utvokst). Skal droppes."""
    rows = []
    for i, d in enumerate([200, 180, 160, 150]):
        rows.append(_line(20, "Pampers Baby-Dry Str. 3", "Bleier", f"sz{i}", d))
    return pd.DataFrame(rows)


@pytest.fixture
def lines_excluded() -> pd.DataFrame:
    """Pant og gavekort — skal alltid filtreres ut."""
    rows = []
    for i, d in enumerate([28, 21, 14, 7]):
        rows.append(_line(30, "Pant pose", "Pant", f"p{i}", d))
    for i, d in enumerate([60, 45, 30, 15]):
        rows.append(_line(31, "Gavekort 500 kr", "Gavekort", f"g{i}", d))
    return pd.DataFrame(rows)
