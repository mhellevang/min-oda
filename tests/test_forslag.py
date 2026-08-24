"""forslag.py: sparetips og nye-varer med injisert chat/search — ingen
ekte LLM- eller Oda-kall."""

import json
import threading
from datetime import datetime

import pandas as pd
import pytest

from min_oda import forslag
from min_oda.handleliste import Rad


@pytest.fixture
def rows():
    return [
        Rad(key="melk", product_id=100, product_name="Tine Lettmelk 1,75 l",
            unit_price=30.0),
        Rad(key="brød", product_id=200, product_name="Solsikkebrød 620 g",
            unit_price=45.0),
        Rad(key="engangs", product_id=300, product_name="Party-lys",
            unit_price=20.0, is_engangs=True),
    ]


@pytest.fixture
def lines():
    return pd.DataFrame({
        "product_id": [100, 100, 100, 110, 200],
        "product_name": ["Tine Lettmelk 1,75 l"] * 3
        + ["Q Skummet melk 1 l", "Solsikkebrød 620 g"],
        "category": ["Meieri, ost og egg"] * 4 + ["Bakeri og konditori"],
        "order_id": [1, 2, 3, 3, 1],
        "quantity": [1] * 5,
        "line_total": [30.0, 30.0, 30.0, 25.0, 45.0],
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


def test_kjopsprofil_viser_variantvalg(lines):
    profil = forslag._kjopsprofil(lines)
    # Begge melkevariantene med ordretall — det er valget LLM-en skal lese.
    assert "melk: Tine Lettmelk 1,75 l (3 ordrer), Q Skummet melk 1 l (1 ordrer)" in profil
    assert "Kokestil:" in profil and "Helse:" in profil


def test_nye_valideres_mot_katalogen(lines):
    svar = json.dumps({
        "profil": ["Velger helmelk-varianter fra Tine."],
        "forslag": [
            {"sok": "fiskesaus", "begrunnelse": "Passer wok-vanene."},
            {"sok": "finnes-ikke", "begrunnelse": "Ingen treff."},
        ],
    })
    # Første treff er urelatert (deler ikke ord med søket) — hoppes over.
    hits = {"fiskesaus": [
        {"product_id": 499, "name": "Sushi Ginger 190 g", "price": 55.0, "image": None},
        {"product_id": 500, "name": "Fiskesaus 200 ml", "price": 35.0, "image": None},
    ]}
    profil, nye = forslag._nye(lines, lambda s, u, max_tokens: svar, _search(hits))
    assert profil == ["Velger helmelk-varianter fra Tine."]
    assert len(nye) == 1
    assert nye[0]["product_id"] == 500
    assert nye[0]["begrunnelse"] == "Passer wok-vanene."


def test_generer_skriver_cache(tmp_path, monkeypatch, rows, lines):
    monkeypatch.setattr(forslag.GENERERING, "fil", tmp_path / "llm_forslag.json")
    svar_sparetips = json.dumps([{"key": "melk", "product_id": 101, "begrunnelse": "ok"}])
    svar_nye = json.dumps({"profil": ["obs"],
                           "forslag": [{"sok": "fiskesaus", "begrunnelse": "ok"}]})
    svar = iter([svar_sparetips, svar_nye])
    hits = {"melk": MELK_HITS,
            "fiskesaus": [{"product_id": 500, "name": "Fiskesaus", "price": 35.0, "image": None}]}
    resultat = forslag.generer(rows, lines,
                               chat=lambda s, u, max_tokens: next(svar),
                               search=_search(hits))
    assert "feil" not in resultat
    assert resultat["sparetips"][0]["product_id"] == 101
    assert resultat["nye"][0]["product_id"] == 500
    assert forslag.GENERERING.last() == resultat


def test_er_ferskt(tmp_path, monkeypatch):
    fil = tmp_path / "llm_forslag.json"
    monkeypatch.setattr(forslag.GENERERING, "fil", fil)
    assert not forslag.GENERERING.er_ferskt()  # ingen fil
    fil.write_text(json.dumps(
        {"generert": datetime.now().isoformat(timespec="minutes")}
    ))
    assert forslag.GENERERING.er_ferskt()
    fil.write_text(json.dumps({"generert": "2026-01-01T00:00"}))
    assert not forslag.GENERERING.er_ferskt()


def test_bakgrunnsjobb_single_flight(tmp_path, monkeypatch, rows, lines):
    monkeypatch.setattr(forslag.GENERERING, "fil", tmp_path / "llm_forslag.json")
    startet = threading.Event()
    slipp = threading.Event()

    def treg_chat(s, u, max_tokens):
        startet.set()
        slipp.wait(5)
        return None

    search = _search({"melk": MELK_HITS})
    t = forslag.start_bakgrunnsjobb(rows, lines, chat=treg_chat, search=search)
    assert t is not None
    assert startet.wait(5)
    assert forslag.GENERERING.er_i_gang()
    # Single-flight: jobb nummer to avvises mens den første kjører.
    assert forslag.start_bakgrunnsjobb(rows, lines, chat=treg_chat, search=search) is None
    slipp.set()
    t.join(5)
    assert not forslag.GENERERING.er_i_gang()
    assert forslag.GENERERING.siste_feil()  # chat ga None -> feilen er registrert


def test_generer_feil_roerer_ikke_cache(tmp_path, monkeypatch, rows, lines):
    fil = tmp_path / "llm_forslag.json"
    fil.write_text('{"generert": "2026-08-01T10:00", "sparetips": [], "nye": []}')
    monkeypatch.setattr(forslag.GENERERING, "fil", fil)
    resultat = forslag.generer(rows, lines,
                               chat=lambda s, u, max_tokens: None,
                               search=_search({"melk": MELK_HITS}))
    assert "feil" in resultat
    assert forslag.GENERERING.last()["generert"] == "2026-08-01T10:00"
