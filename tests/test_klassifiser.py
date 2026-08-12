"""klassifiser.py: kandidat-utvalg og forankring av LLM-svar."""

import json

import pandas as pd

from min_oda import klassifiser


def _lines():
    return pd.DataFrame({
        "product_id": [1, 2, 3, 3],
        "product_name": [
            "Tine Lettmelk 1,75 l",      # treffer keyword-regel (melk) — ut
            "Mystisk Spesialitet 300 g",  # ingen regel — kandidat
            "Rar Delikatesse 100 g",      # ingen regel — kandidat (dedupes)
            "Rar Delikatesse 100 g",
        ],
        "category": ["Meieri, ost og egg", "Mathall", "Mathall", "Mathall"],
    })


def test_finn_kandidater(monkeypatch):
    monkeypatch.setattr(klassifiser, "_explicit_mapping", lambda: {3: "delikatesse"})
    kand = klassifiser.finn_kandidater(_lines())
    # 1 har keyword-treff, 3 har eksplisitt mapping — bare 2 står igjen.
    assert [k["product_id"] for k in kand] == [2]


def test_foreslaa_typer_forankres(monkeypatch):
    monkeypatch.setattr(klassifiser, "_explicit_mapping", lambda: {})
    kand = [{"product_id": 2, "name": "Mystisk Spesialitet", "category": "Mathall"}]
    svar = json.dumps({"2": "Spesialitet", "999": "hallusinert"})
    forslag = klassifiser.foreslaa_typer(kand, chat=lambda s, u, max_tokens: svar)
    assert forslag == {2: "spesialitet"}  # ukjent pid droppes, type lowercases
