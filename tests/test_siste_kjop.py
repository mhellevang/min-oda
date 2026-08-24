"""siste_kjop.py: pris og bilde fra nyeste ordre som har verdien.

Slått sammen fra test_prices.py og test_images.py — de to modulene var
samme sortering og dedupe med ulikt kolonnenavn."""

from __future__ import annotations

import pandas as pd

from min_oda.siste_kjop import siste_bilder, siste_priser


def test_pris_fra_nyeste_ordre():
    """Et produkt med flere kjøp får prisen fra nyeste ordre, ikke eldste."""
    df = pd.DataFrame({
        "product_id": [1, 1, 2],
        "unit_price": [10.0, 12.0, 5.0],
        "date": pd.to_datetime(
            ["2026-01-01", "2026-02-01", "2026-01-15"], utc=True
        ),
    })
    assert siste_priser(df) == {1: 12.0, 2: 5.0}


def test_pris_hopper_over_rader_uten_pris():
    df = pd.DataFrame({
        "product_id": [1, 2],
        "unit_price": [None, 7.5],
        "date": pd.to_datetime(["2026-01-01", "2026-01-02"], utc=True),
    })
    assert siste_priser(df) == {2: 7.5}


def test_uten_date_kolonne_gjelder_rekkefolgen_i_dataene():
    df = pd.DataFrame({"product_id": [1, 1], "unit_price": [10.0, 20.0]})
    assert siste_priser(df) == {1: 20.0}


def test_bilde_fra_nyeste_ordre():
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
    assert siste_bilder(df) == {
        1: "https://images.oda.com/ny.jpg",
        2: "https://images.oda.com/annen.jpg",
    }


def test_bilde_hopper_over_rader_uten_bilde():
    df = pd.DataFrame({
        "product_id": [1, 2],
        "product_image": [None, "https://images.oda.com/x.jpg"],
        "date": pd.to_datetime(["2026-01-01", "2026-01-02"], utc=True),
    })
    assert siste_bilder(df) == {2: "https://images.oda.com/x.jpg"}


def test_bilde_ignorerer_verdier_som_ikke_er_url():
    """NaN som ble til strengen 'nan' i CSV-runden skal ikke slippe gjennom."""
    df = pd.DataFrame({
        "product_id": [1, 2],
        "product_image": ["nan", "https://images.oda.com/x.jpg"],
    })
    assert siste_bilder(df) == {2: "https://images.oda.com/x.jpg"}


def test_eldre_gyldig_bilde_vinner_over_nyere_ugyldig():
    """Er nyeste ordres bilde ubrukelig, faller vi tilbake til forrige
    ordre som hadde et — filtreringen skjer før dedupen."""
    df = pd.DataFrame({
        "product_id": [1, 1],
        "product_image": ["https://images.oda.com/ok.jpg", "nan"],
        "date": pd.to_datetime(["2026-01-01", "2026-02-01"], utc=True),
    })
    assert siste_bilder(df) == {1: "https://images.oda.com/ok.jpg"}


def test_tom_input_og_manglende_kolonne():
    assert siste_priser(pd.DataFrame()) == {}
    assert siste_bilder(pd.DataFrame()) == {}
    assert siste_bilder(pd.DataFrame({"product_id": [1], "product_name": ["Melk"]})) == {}
