"""Tester for curate — bygger handleliste fra kadens."""

from __future__ import annotations

import pandas as pd
import pytest

from min_oda.build_list import curate

TODAY = pd.Timestamp("2026-05-14", tz="UTC")


def _line(pid, name, cat, oid, days_ago):
    return {
        "product_id": pid, "product_name": name, "category": cat,
        "order_id": oid, "date": TODAY - pd.Timedelta(days=days_ago),
        "quantity": 1, "line_total": 25.0,
    }


@pytest.fixture
def lines_two_brands_of_milk() -> pd.DataFrame:
    """TINE-melk: 4 ordrer over flere måneder.
    Q-melk: 1 ordre, kjøpt for 3 dager siden (det nyeste).
    Representativ skal være TINE (flest distinkte ordrer), ikke Q."""
    rows = []
    for i, d in enumerate([60, 40, 20, 10]):
        rows.append(_line(100, "TINE Lettmelk 1 L", "Meieri", f"t{i}", d))
    rows.append(_line(101, "Q-Meieriene Lettmelk 1 L", "Meieri", "q0", 3))
    return pd.DataFrame(rows)


@pytest.fixture
def lines_many_meieri() -> pd.DataFrame:
    """Mange ulike varetyper alle i kategorien Meieri."""
    rows = []
    types = [
        (200, "TINE Lettmelk 1 L"),
        (201, "TINE Skyr Naturell"),
        (202, "TINE Kefir Naturell"),
        (203, "TINE Crème Fraîche 18 %"),
        (204, "TINE Rømme 18 %"),
        (205, "TINE Smør Meierismør"),
    ]
    for pid, name in types:
        for i, d in enumerate([28, 21, 14, 7]):
            rows.append(_line(pid, name, "Meieri", f"{pid}-{i}", d))
    return pd.DataFrame(rows)


def test_representative_is_most_distinct_orders(lines_two_brands_of_milk):
    """For varetype "melk" finnes to merker. Det med flest distinkte
    ordrer skal velges som representant, selv om det andre er nyere."""
    out = curate(lines_two_brands_of_milk)
    melk = out[out["key"] == "melk"]
    assert len(melk) == 1
    assert "TINE" in str(melk["product_name"].iloc[0])


def test_max_per_category_caps_rows(lines_many_meieri):
    """Med 6 ulike varetyper i Meieri og max_per_category=3, skal vi få
    kun 3 rader."""
    out = curate(lines_many_meieri, max_per_category=3, top_n=50)
    assert len(out) == 3
    assert (out["category"] == "Meieri").all()


def test_top_n_caps_total(lines_many_meieri):
    """top_n trumfer per-kategori-grensen for totalt antall rader."""
    out = curate(lines_many_meieri, max_per_category=10, top_n=2)
    assert len(out) == 2


def test_foreslatt_antall_scales_with_cycle(lines_many_meieri):
    """qty = ceil(cycle * snitt_per_besøk / median). Med ukentlig kadens,
    snitt 1 enhet per besøk og 14-dagers syklus skal forslag være 2 per vare."""
    out = curate(lines_many_meieri, list_cycle_days=14, max_per_category=10)
    assert (out["foreslått_antall"] >= 2).all()
    out7 = curate(lines_many_meieri, list_cycle_days=7, max_per_category=10)
    assert (out7["foreslått_antall"] == 1).all()


def test_foreslatt_antall_respects_quantity_per_visit():
    """En bruker som kjøper 3 melk hver uke skal få 6 melk for 14 dager,
    ikke 2. Antall-feltet må ta hensyn til hvor mye som faktisk handles
    per besøk, ikke bare hvor ofte."""
    rows = []
    for i, d in enumerate([28, 21, 14, 7]):
        rows.append({
            "product_id": 700, "product_name": "TINE Lettmelk 1 L",
            "category": "Meieri", "order_id": f"o{i}",
            "date": TODAY - pd.Timedelta(days=d),
            "quantity": 3, "line_total": 75.0,
        })
    out = curate(pd.DataFrame(rows), list_cycle_days=14, max_per_category=10)
    melk = out[out["key"] == "melk"]
    assert int(melk["foreslått_antall"].iloc[0]) == 6
    # Halv syklus → halvt forbruk, fortsatt rundet opp.
    out7 = curate(pd.DataFrame(rows), list_cycle_days=7, max_per_category=10)
    assert int(out7[out7["key"] == "melk"]["foreslått_antall"].iloc[0]) == 3


def test_category_priority_orders_rows():
    """Bleier kommer før Snacks i CATEGORY_PRIORITY — første rad skal være
    fra Bleier når begge er kandidater."""
    rows = []
    # Bleier: navn må matche \bbleie-regelen for å klassifiseres.
    for i, d in enumerate([28, 21, 14, 7]):
        rows.append(_line(300, "Libero Bleier Comfort", "Bleier", f"bl{i}", d))
    # Snacks: stabilt med navn som hopper på en av snacks-reglene.
    for i, d in enumerate([28, 21, 14, 7]):
        rows.append(_line(301, "Maarud Potetgull Salt", "Snacks", f"sn{i}", d))
    out = curate(pd.DataFrame(rows), max_per_category=5, top_n=10)
    assert out["category"].iloc[0] == "Bleier"


def test_baby_type_promoted_over_deal_category():
    """Bleier som Oda har plassert under 'Faste, gode deals' skal fortsatt
    prioriteres høyt — uten overstyringen havner alle bleier-størrelser
    bakerst og blir presset ut av top_n-cuten når det er konkurranse."""
    rows = []
    for i, d in enumerate([28, 21, 14, 7]):
        rows.append(_line(400, "Libero bleie Str. 3, 5-9 kg", "Faste, gode deals", f"d3-{i}", d))
    for i, d in enumerate([28, 21, 14, 7]):
        rows.append(_line(401, "Libero bleie Str. 6, 15-30kg", "Faste, gode deals", f"d6-{i}", d))
    # Et lavprioritert fyllprodukt — hvis bleier ikke prioriteres ender
    # det opp før dem.
    for i, d in enumerate([28, 21, 14, 7]):
        rows.append(_line(402, "Maarud Potetgull Salt", "Snacks", f"sn{i}", d))
    out = curate(pd.DataFrame(rows), max_per_category=5, top_n=2)
    keys = list(out["key"])
    assert "bleier-str3" in keys
    assert "bleier-str6" in keys
