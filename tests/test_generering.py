"""generering.py: livsløpet rundt en LLM-generering — single-flight,
feilregistrering, ferskhet og template-konteksten. Dekket bare indirekte
før, gjennom én test i test_forslag.py."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta

import pytest

from min_oda import llm
from min_oda.generering import Generering


@pytest.fixture
def gen(tmp_path):
    return Generering("test-jobb", tmp_path / "gen.json", fersk_timer=24.0)


@pytest.fixture
def med_llm(monkeypatch):
    """kontekst() skjuler alt uten provider — lat som vi har en."""
    monkeypatch.setattr(llm, "enabled", lambda: True)
    monkeypatch.setattr(llm, "provider_label", lambda: "test-provider")


def test_lagre_stempler_tidspunkt_og_provider(gen, med_llm):
    lagret = gen.lagre({"tips": ["noe"]})

    assert lagret["provider"] == "test-provider"
    assert lagret["generert"]
    assert gen.last() == lagret
    assert json.loads(gen.fil.read_text())["tips"] == ["noe"]


def test_uten_kjoring_er_alt_tomt(gen):
    assert gen.last() is None
    assert gen.er_ferskt() is False
    assert gen.er_i_gang() is False
    assert gen.siste_feil() is None


def test_ferskhet_maales_paa_generert_tidspunkt(gen):
    gen.fil.write_text(json.dumps({"generert": datetime.now().isoformat()}))
    assert gen.er_ferskt()

    gammel = (datetime.now() - timedelta(hours=30)).isoformat()
    gen.fil.write_text(json.dumps({"generert": gammel}))
    assert not gen.er_ferskt()
    assert gen.er_ferskt(max_age_hours=48)


def test_ulesbar_cache_er_ikke_fersk(gen):
    gen.fil.write_text("{ ikke json")
    assert gen.last() is None
    assert not gen.er_ferskt()


def test_single_flight_avviser_ny_kjoring(gen):
    slipp = threading.Event()

    def treg() -> dict:
        slipp.wait(2)
        return {}

    t = gen.start(treg)
    assert t is not None
    assert gen.er_i_gang()
    assert gen.start(treg) is None  # én om gangen

    slipp.set()
    t.join(timeout=2)
    assert not gen.er_i_gang()
    assert gen.siste_feil() is None


def test_feilnokkel_blir_siste_feil(gen):
    t = gen.start(lambda: {"feil": "gikk ikke"})
    t.join(timeout=2)

    assert gen.siste_feil() == "gikk ikke"
    assert not gen.er_i_gang()


def test_unntak_laser_ikke_jobben(gen):
    def kraster() -> dict:
        raise RuntimeError("boom")

    gen.start(kraster).join(timeout=2)

    assert not gen.er_i_gang()
    assert "boom" in gen.siste_feil()

    gen.start(lambda: {}).join(timeout=2)
    assert gen.siste_feil() is None  # ny vellykket kjøring nullstiller


def test_kontekst_gir_templatens_tre_navn(gen, med_llm):
    gen.lagre({"tips": []})
    ctx = gen.kontekst("forslag")

    assert set(ctx) == {"forslag", "forslag_kjorer", "forslag_feil"}
    assert ctx["forslag"]["tips"] == []
    assert ctx["forslag_kjorer"] is False


def test_kontekst_uten_provider_skjuler_alt(gen, monkeypatch):
    monkeypatch.setattr(llm, "enabled", lambda: False)
    gen.fil.write_text(json.dumps({"generert": datetime.now().isoformat()}))

    assert gen.kontekst("forslag") == {
        "forslag": None, "forslag_kjorer": False, "forslag_feil": None,
    }


def test_kontekst_starter_bare_naar_cachen_er_gammel(gen, med_llm):
    startet = []
    gen.lagre({})
    gen.kontekst("forslag", lambda: startet.append(1))
    assert startet == []  # nettopp generert

    gen.fil.write_text(json.dumps(
        {"generert": (datetime.now() - timedelta(hours=30)).isoformat()}
    ))
    gen.kontekst("forslag", lambda: startet.append(1))
    assert startet == [1]
