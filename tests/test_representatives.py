"""Tester for valgt representant: lagring, klassifiserings-pinning og
curate-overstyring."""

from __future__ import annotations

import pandas as pd
import pytest

from min_oda import representatives
from min_oda.build_list import curate
from min_oda.product_types import product_type

TODAY = pd.Timestamp("2026-05-14", tz="UTC")


def _line(pid, name, cat, oid, days_ago):
    return {
        "product_id": pid, "product_name": name, "category": cat,
        "order_id": oid, "date": TODAY - pd.Timedelta(days=days_ago),
        "quantity": 1, "line_total": 25.0,
    }


@pytest.fixture
def tmp_chosen(tmp_path, monkeypatch):
    """Pek CHOSEN_PATH mot en tmp-fil og nullstill pinned-cachen."""
    path = tmp_path / "chosen_products.json"
    monkeypatch.setattr(representatives, "CHOSEN_PATH", path)
    monkeypatch.setattr(representatives, "_PINNED_CACHE", None)
    return path


@pytest.fixture
def lines_diapers() -> pd.DataFrame:
    """Bleier str. 6 med stabil kadens — mest kjøpte variant er pid 500."""
    rows = []
    for i, d in enumerate([28, 21, 14, 7]):
        rows.append(_line(500, "R Lev Vel bleier XL Str. 6, 15-30kg", "Bleier", f"d{i}", d))
    return pd.DataFrame(rows)


def test_choose_unchoose_roundtrip(tmp_chosen):
    representatives.choose("bleier-str6", 65006,
                           name="R Lev Vel buksebleier Str. 6", price=21.45)
    chosen = representatives.chosen_representatives()
    assert chosen["bleier-str6"]["product_id"] == 65006
    assert chosen["bleier-str6"]["price"] == 21.45

    representatives.unchoose("bleier-str6")
    assert representatives.chosen_representatives() == {}


def test_pinned_types_overrides_classification(tmp_chosen):
    """Et valgt produkt klassifiseres til varetypen det ble valgt for,
    komplett nøkkel uten ny suffiks-utledning."""
    representatives.choose("bleier-str6", 999999, name="Testvare")
    assert product_type("Noe helt annet Str. 3", "Baby og barn", 999999) == "bleier-str6"
    # Andre produkter er upåvirket.
    assert product_type("Libero Bleier Comfort Str. 3", "Bleier", 12345) == "bleier-str3"


def test_pinned_cache_refreshes_on_write(tmp_chosen):
    representatives.choose("melk", 111, name="A")
    assert representatives.pinned_types() == {111: "melk"}
    representatives.unchoose("melk")
    assert representatives.pinned_types() == {}


def test_curate_chosen_overrides_representative(tmp_chosen, lines_diapers):
    """curate skal vise den valgte katalogvaren i stedet for den mest
    kjøpte varianten, med kadens fra varetypen."""
    chosen = {"bleier-str6": {"product_id": 65006,
                              "name": "R Lev Vel buksebleier Str. 6"}}
    out = curate(lines_diapers, chosen=chosen, today=TODAY)
    row = out[out["key"] == "bleier-str6"].iloc[0]
    assert int(row["product_id"]) == 65006
    assert row["product_name"] == "R Lev Vel buksebleier Str. 6"
    assert row["foreslått_antall"] >= 1

    # Uten chosen: mest kjøpte variant som før.
    out2 = curate(lines_diapers, today=TODAY)
    assert int(out2[out2["key"] == "bleier-str6"].iloc[0]["product_id"]) == 500
