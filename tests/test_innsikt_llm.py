"""innsikt_llm.py: mønster-verifisering mot faktiske ordrer og
deterministiske «siden sist»-fakta. Injisert chat — ingen ekte LLM-kall."""

import json

import pandas as pd
import pytest

from min_oda import innsikt_llm
from min_oda.product_types import annotate

TODAY = pd.Timestamp("2026-05-14")


@pytest.fixture
def lines():
    """12 ordrer over 12 uker: melk i alle (stift), taco-kombo (tortilla +
    karbonadedeig + salsa + mais) i ordre 1/3/5/7, ellers variert."""
    rows = []
    for i in range(12):
        oid = i + 1
        dato = TODAY - pd.Timedelta(days=7 * (12 - i))
        def add(pid, navn, kat):
            rows.append({
                "order_id": oid, "date": dato, "product_id": pid,
                "product_name": navn, "category": kat,
                "quantity": 1, "line_total": 30.0,
            })
        add(1, "Tine Lettmelk 1,75 l", "Meieri, ost og egg")
        if oid in (1, 3, 5, 7):
            add(10, "Old El Paso Tortilla 8 stk", "Middager og tilbehør")
            add(11, "Gilde Karbonadedeig 5%", "Kylling og kjøtt")
            add(12, "Old El Paso Salsa Medium", "Middager og tilbehør")
            add(13, "Green Giant Mais", "Middager og tilbehør")
        else:
            add(20 + oid, f"Solsikkebrød {oid}", "Bakeri og konditori")
            add(40 + oid, "Gulrot Norge", "Frukt og grønt")
            add(60 + oid, "Prior Egg Str M", "Meieri, ost og egg")
    return pd.DataFrame(rows)


def test_monstre_verifiseres_mot_ordrene(lines):
    df = annotate(lines)
    svar = json.dumps([
        {"navn": "Taco-kveld",
         "varetyper": ["tortilla-lompe", "kjøttdeig", "taco", "mais"],
         "kommentar": "Klassisk taco-oppsett."},
        {"navn": "Hallusinert sushi",
         "varetyper": ["fisk-fersk", "ris"],
         "kommentar": "Finnes ikke i ordrene."},
    ])
    monstre = innsikt_llm._monstre(df, lambda s, u, max_tokens: svar)
    assert len(monstre) == 1
    m = monstre[0]
    assert m["navn"] == "Taco-kveld"
    assert m["n_ordrer"] == 4          # regnet av oss, ikke påstått av LLM-en
    assert m["intervall_dager"] == 14  # annenhver uke
    assert m["sist"] == "2026-04-02"  # ordre 7 = 42 dager før TODAY


def test_monstre_stifter_filtreres_fra_prompten(lines):
    df = annotate(lines)
    prompts = []

    def chat(s, u, max_tokens):
        prompts.append(u)
        return "[]"

    innsikt_llm._monstre(df, chat)
    # melk er i alle 12 ordrer -> skjult for LLM-en; taco-varene skal vises.
    assert "melk" not in prompts[0]
    assert "tortilla-lompe" in prompts[0]


def test_monstre_llm_svikt_gir_none(lines):
    df = annotate(lines)
    assert innsikt_llm._monstre(df, lambda s, u, max_tokens: None) is None


def test_fakta_siden_sist():
    rows = []
    # Ny gjenganger: skyr i 3 ordrer siste 30 dager.
    for i, dager in enumerate((25, 15, 5)):
        rows.append({
            "order_id": 100 + i, "date": TODAY - pd.Timedelta(days=dager),
            "product_id": 1, "product_name": "Q Skyr Naturell",
            "category": "Meieri, ost og egg", "quantity": 1, "line_total": 25.0,
        })
    # Stift på vei ut: brød ukentlig i 6 ordrer, så 40 dagers stillhet.
    for i in range(6):
        rows.append({
            "order_id": 200 + i, "date": TODAY - pd.Timedelta(days=40 + 7 * i),
            "product_id": 2, "product_name": "Korn Solsikkebrød",
            "category": "Bakeri og konditori", "quantity": 1, "line_total": 45.0,
        })
    df = annotate(pd.DataFrame(rows))
    fakta = innsikt_llm._fakta_siden_sist(df, TODAY)
    assert any("Ny gjenganger" in f and "Skyr" in f for f in fakta)
    assert any("Stift på vei ut" in f and "Solsikkebrød" in f for f in fakta)


def test_generer_skriver_cache(tmp_path, monkeypatch, lines):
    monkeypatch.setattr(innsikt_llm, "INNSIKT_FILE", tmp_path / "innsikt_llm.json")
    svar = iter([
        json.dumps([{"navn": "Taco-kveld",
                     "varetyper": ["tortilla-lompe", "kjøttdeig", "taco"],
                     "kommentar": "ok"}]),
        "Ingenting vesentlig har endret seg.",
    ])
    r = innsikt_llm.generer(lines, chat=lambda s, u, max_tokens: next(svar, None),
                            today=TODAY)
    assert "feil" not in r
    assert r["monstre"][0]["navn"] == "Taco-kveld"
    assert innsikt_llm.load_innsikt() == r
