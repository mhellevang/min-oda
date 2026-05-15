"""Tester for compute_cadence — kantsakene som ikke er åpenbare fra koden."""

from __future__ import annotations

import pandas as pd

from min_oda.restock import compute_cadence


def test_baseline_picks_up_staples(lines_milk_and_bread, today):
    out = compute_cadence(lines_milk_and_bread, today=today)
    keys = set(out["key"])
    assert "melk" in keys
    assert "brød" in keys


def test_min_buys_filter(lines_milk_and_bread, today):
    """En vare med færre enn min_buys kjøp skal ikke regnes som fast vare."""
    rare = pd.DataFrame([
        {"product_id": 99, "product_name": "Sjelden saus", "category": "Krydder",
         "order_id": "x", "date": today - pd.Timedelta(days=10), "quantity": 1, "line_total": 30.0},
    ])
    lines = pd.concat([lines_milk_and_bread, rare], ignore_index=True)
    out = compute_cadence(lines, min_buys=3, today=today)
    assert 99 not in set(out["key"]) if not out.empty else True
    # by_type=True default — sjekk på navn også
    assert not any("sjelden" in str(n).lower() for n in out["product_name"])


def test_max_median_filter(lines_rare, today):
    """Median > max_median_days skal droppes som "sjeldent kjøp"."""
    out = compute_cadence(lines_rare, today=today, max_median_days=90)
    assert out.empty


def test_abandoned_dropped(lines_abandoned, today):
    """Sist kjøpt for over abandon_factor * median dager siden → droppes."""
    out = compute_cadence(lines_abandoned, today=today)
    assert out.empty


def test_size_coded_dropped_when_stale(lines_size_coded, today):
    """Størrelses-kodede produkter (str. N) droppes hvis sist kjøp er over
    SIZE_CODED_MAX_AGE_DAYS gammelt. Bleier i str. 3 sist kjøpt for 5 mnd
    siden skal være ute."""
    out = compute_cadence(lines_size_coded, today=today)
    assert out.empty


def test_excluded_keywords(lines_excluded, today):
    """Pant og gavekort skal alltid filtreres ut, uansett kadens."""
    out = compute_cadence(lines_excluded, today=today)
    names = [str(n).lower() for n in out["product_name"]] if not out.empty else []
    assert not any("pant" in n for n in names)
    assert not any("gavekort" in n for n in names)


def test_substitution_when_by_type(lines_substitution, today):
    """To merker av samme type slås sammen til ett kadens-tall når by_type=True."""
    out = compute_cadence(lines_substitution, today=today, by_type=True, min_buys=3)
    melk = out[out["key"] == "melk"]
    assert len(melk) == 1
    # 6 ulike ordrer slått sammen til én melk-rad
    assert int(melk["n_buys"].iloc[0]) == 6


def test_status_thresholds(lines_milk_and_bread, today):
    """Status kategoriseres på avstand til neste forfall:
        forfalt:    days_until_due < -median * 0.5
        akkurat nå: -median*0.5 <= d < 0
        snart:      0 <= d <= 7
        i rute:     d > 7
    """
    out = compute_cadence(lines_milk_and_bread, today=today)
    valid = {"forfalt", "akkurat nå", "snart", "i rute"}
    assert set(out["status"]).issubset(valid)
    # Med melk hver 7. d sist kjøpt for 7 d siden er status enten "snart"
    # eller "akkurat nå" — uansett ikke "i rute".
    melk = out[out["key"] == "melk"]
    assert melk["status"].iloc[0] != "i rute"


def test_today_parameter_is_respected(lines_milk_and_bread):
    """today-parameteret skal være eneste kilde til "nå" — ingen wall clock."""
    future = pd.Timestamp("2027-01-01", tz="UTC")
    out = compute_cadence(lines_milk_and_bread, today=future)
    # Alt skal være forfalt 7+ mnd etter siste kjøp.
    if not out.empty:
        assert (out["status"] == "forfalt").all()


def test_recency_window_biases_to_recent_pattern(today):
    """Husstand som tidligere kjøpte melk månedlig og nylig har gått over
    til ukentlig — kadensen skal reflektere det nye ukentlige mønsteret,
    ikke gjennomsnittet over alle år."""
    rows = []
    # 15 historiske månedlige kjøp (qty 1) — fra 600 til 180 dager siden.
    for i, d in enumerate(range(600, 179, -30)):
        rows.append({
            "product_id": 800, "product_name": "TINE Lettmelk 1 L",
            "category": "Meieri", "order_id": f"old-{i}",
            "date": today - pd.Timedelta(days=d),
            "quantity": 1, "line_total": 25.0,
        })
    # 23 nylige ukentlige kjøp (qty 3) — fra 154 til 0 dager siden.
    for i, d in enumerate(range(154, -1, -7)):
        rows.append({
            "product_id": 800, "product_name": "TINE Lettmelk 1 L",
            "category": "Meieri", "order_id": f"new-{i}",
            "date": today - pd.Timedelta(days=d),
            "quantity": 3, "line_total": 75.0,
        })
    out = compute_cadence(pd.DataFrame(rows), today=today, recency_events=20)
    melk = out[out["key"] == "melk"].iloc[0]
    # Nylig mønster: ukentlig, 3 melk hver gang.
    assert melk["median_days"] == 7
    assert melk["avg_qty_per_event"] == 3.0
    # n_buys speiler hele historikken — stabilitet skal ikke svekkes.
    assert melk["n_buys"] == 15 + 23


def test_recency_none_uses_full_history(today):
    """Med recency_events=None skal kadensen være som før (hele historikken)."""
    rows = []
    for i, d in enumerate([28, 21, 14, 7]):
        rows.append({
            "product_id": 900, "product_name": "TINE Lettmelk 1 L",
            "category": "Meieri", "order_id": f"o{i}",
            "date": today - pd.Timedelta(days=d),
            "quantity": 2, "line_total": 50.0,
        })
    out = compute_cadence(pd.DataFrame(rows), today=today, recency_events=None)
    melk = out[out["key"] == "melk"].iloc[0]
    assert melk["median_days"] == 7
    assert melk["avg_qty_per_event"] == 2.0


def test_dropna_handled_internally(lines_milk_and_bread, today):
    """compute_cadence skal kunne ta rader med NaN-er uten å krasje —
    den dropna-er på sine egne preconditions."""
    dirty = lines_milk_and_bread.copy()
    bad = pd.DataFrame([{
        "product_id": None, "product_name": None, "category": None,
        "order_id": "bad", "date": None, "quantity": 1, "line_total": 10.0,
    }])
    lines = pd.concat([dirty, bad], ignore_index=True)
    out = compute_cadence(lines, today=today)
    assert not out.empty  # de gode radene overlever
