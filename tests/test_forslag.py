"""forslag.py: sparetips og nye-varer med injisert chat/search — ingen
ekte LLM- eller Oda-kall."""

import json

import pandas as pd
import pytest

from min_oda import forslag


@pytest.fixture
def rows():
    return [
        {"key": "melk", "product_id": 100, "product_name": "Tine Lettmelk 1,75 l",
         "unit_price": 30.0},
        {"key": "brød", "product_id": 200, "product_name": "Solsikkebrød 620 g",
         "unit_price": 45.0},
        {"key": "engangs", "product_id": 300, "product_name": "Party-lys",
         "unit_price": 20.0, "is_engangs": True},
    ]


@pytest.fixture
def lines():
    return pd.DataFrame({
        "product_name": ["Tine Lettmelk 1,75 l"] * 3 + ["Solsikkebrød 620 g"] * 2,
        "order_id": [1, 2, 3, 1, 2],
    })


def _search(hits_per_query):
    def search(query, limit=12):
        return hits_per_query.get(query, [])[:limit]
    return search


MELK_HITS = [
    {"product_id": 101, "name": "First Price Lettmelk 1,75 l", "price": 22.0, "image": None},
    {"product_id": 102, "name": "Q Lettmelk 0,5 l", "price": 15.0, "image": None},
    {"product_id": 103, "name": "Dyr økomelk", "price": 55.0, "image": None},  # dyrere — filtreres
]


def test_sparetips_kandidater_kun_billigere(rows):
    kand = forslag._sparetips_kandidater(rows, _search({"melk": MELK_HITS}))
    assert len(kand) == 1  # brød uten treff, engangs hoppes over
    assert kand[0]["key"] == "melk"
    assert [t["product_id"] for t in kand[0]["treff"]] == [101, 102]


def test_sparetips_forankres_i_kandidatene(rows):
    # LLM-en svarer med ett gyldig valg og ett produkt som ikke var kandidat
    # (hallusinert id) — bare det gyldige overlever.
    svar = json.dumps([
        {"key": "melk", "product_id": 101, "begrunnelse": "Samme mengde, lavere pris."},
        {"key": "melk", "product_id": 999, "begrunnelse": "Finnes ikke."},
    ])
    tips = forslag._sparetips(rows, lambda s, u, max_tokens: svar,
                              _search({"melk": MELK_HITS}))
    assert len(tips) == 1
    assert tips[0]["product_id"] == 101
    assert tips[0]["fra_navn"] == "Tine Lettmelk 1,75 l"
    assert tips[0]["pris"] == 22.0


def test_sparetips_llm_svikt_gir_none(rows):
    assert forslag._sparetips(rows, lambda s, u, max_tokens: None,
                              _search({"melk": MELK_HITS})) is None


def test_sparetips_uten_kandidater_gir_tom_liste(rows):
    assert forslag._sparetips(rows, lambda s, u, max_tokens: "[]",
                              _search({})) == []


def test_nye_valideres_mot_katalogen(lines):
    svar = json.dumps([
        {"sok": "fiskesaus", "begrunnelse": "Passer wok-vanene."},
        {"sok": "finnes-ikke", "begrunnelse": "Ingen treff."},
    ])
    hits = {"fiskesaus": [{"product_id": 500, "name": "Fiskesaus 200 ml",
                           "price": 35.0, "image": None}]}
    nye = forslag._nye(lines, lambda s, u, max_tokens: svar, _search(hits))
    assert len(nye) == 1
    assert nye[0]["product_id"] == 500
    assert nye[0]["begrunnelse"] == "Passer wok-vanene."


def test_generer_skriver_cache(tmp_path, monkeypatch, rows, lines):
    monkeypatch.setattr(forslag, "FORSLAG_FILE", tmp_path / "llm_forslag.json")
    svar_sparetips = json.dumps([{"key": "melk", "product_id": 101, "begrunnelse": "ok"}])
    svar_nye = json.dumps([{"sok": "fiskesaus", "begrunnelse": "ok"}])
    svar = iter([svar_sparetips, svar_nye])
    hits = {"melk": MELK_HITS,
            "fiskesaus": [{"product_id": 500, "name": "Fiskesaus", "price": 35.0, "image": None}]}
    resultat = forslag.generer(rows, lines,
                               chat=lambda s, u, max_tokens: next(svar),
                               search=_search(hits))
    assert "feil" not in resultat
    assert resultat["sparetips"][0]["product_id"] == 101
    assert resultat["nye"][0]["product_id"] == 500
    assert forslag.load_forslag() == resultat


def test_generer_feil_roerer_ikke_cache(tmp_path, monkeypatch, rows, lines):
    fil = tmp_path / "llm_forslag.json"
    fil.write_text('{"generert": "2026-08-01T10:00", "sparetips": [], "nye": []}')
    monkeypatch.setattr(forslag, "FORSLAG_FILE", fil)
    resultat = forslag.generer(rows, lines,
                               chat=lambda s, u, max_tokens: None,
                               search=_search({"melk": MELK_HITS}))
    assert "feil" in resultat
    assert forslag.load_forslag()["generert"] == "2026-08-01T10:00"
