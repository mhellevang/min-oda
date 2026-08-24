"""lager.py: at det som er avledet av en endring faktisk regnes på nytt,
og at det som ikke er, får stå. Ingen test dekket dette før — regelen
fantes bare som en kommentar."""

from __future__ import annotations

import pandas as pd
import pytest

from min_oda.web import lager


@pytest.fixture(autouse=True)
def tomt_lager():
    lager.endret(lager.DATA)
    yield
    lager.endret(lager.DATA)


def _teller(monkeypatch, navn: str, svar):
    """Bytt ut en beregning med en som teller kallene sine."""
    kall: list[int] = []

    def fake(*_args, **_kwargs):
        kall.append(1)
        return svar

    monkeypatch.setattr(lager, navn, fake)
    return kall


def test_kadens_beregnes_en_gang_per_datasett(monkeypatch):
    monkeypatch.setattr(lager, "lines", lambda: pd.DataFrame())
    kall = _teller(monkeypatch, "compute_cadence", pd.DataFrame())

    lager.kadens()
    lager.kadens()
    assert len(kall) == 1


def test_blokkering_rorer_baseline_men_ikke_kadensen(monkeypatch):
    monkeypatch.setattr(lager, "lines", lambda: pd.DataFrame())
    kadens_kall = _teller(monkeypatch, "compute_cadence", pd.DataFrame())
    baseline_kall = _teller(monkeypatch, "curate", pd.DataFrame())

    lager.kadens()
    lager.baseline_ids()
    lager.endret(lager.BLOKKERING)
    lager.kadens()
    lager.baseline_ids()

    assert len(kadens_kall) == 1    # blokkering endrer ikke kjøpsrytmen
    assert len(baseline_kall) == 2  # men hva som er med ved default-filtre


def test_valgt_representant_rorer_bade_baseline_og_kadens(monkeypatch):
    """Pinningen kan omklassifisere et produkt som finnes i historikken,
    så kadensen per varetype blir en annen."""
    monkeypatch.setattr(lager, "lines", lambda: pd.DataFrame())
    kadens_kall = _teller(monkeypatch, "compute_cadence", pd.DataFrame())
    baseline_kall = _teller(monkeypatch, "curate", pd.DataFrame())

    lager.kadens()
    lager.baseline_ids()
    lager.endret(lager.REPRESENTANT)
    lager.kadens()
    lager.baseline_ids()

    assert len(kadens_kall) == 2
    assert len(baseline_kall) == 2


def test_ny_data_nullstiller_alt(monkeypatch):
    data = (pd.DataFrame(), pd.DataFrame())
    last_kall = _teller(monkeypatch, "load_both", data)
    kurv_kall = _teller(monkeypatch, "fetch_cart", pd.DataFrame())
    monkeypatch.setattr(lager, "build_client", lambda: None)

    lager.lines()
    lager.kurv()
    lager.endret(lager.DATA)
    lager.lines()
    lager.kurv()

    assert len(last_kall) == 2
    assert len(kurv_kall) == 2


def test_kurv_hentes_paa_nytt_etter_at_vi_la_noe_i_den(monkeypatch):
    kurv_kall = _teller(monkeypatch, "fetch_cart", pd.DataFrame())
    monkeypatch.setattr(lager, "build_client", lambda: None)

    lager.kurv()
    lager.kurv()
    assert len(kurv_kall) == 1

    lager.endret(lager.KURV)
    lager.kurv()
    assert len(kurv_kall) == 2


def test_manglende_cookies_gir_tom_kurv(monkeypatch):
    from min_oda.oda_client import MissingCredentials

    def uten_cookies(*_a, **_k):
        raise MissingCredentials("ingen cookies")

    monkeypatch.setattr(lager, "build_client", lambda: None)
    monkeypatch.setattr(lager, "fetch_cart", uten_cookies)

    assert lager.kurv().empty


def test_ukjent_kilde_er_en_feil():
    with pytest.raises(ValueError):
        lager.endret("noe helt annet")
