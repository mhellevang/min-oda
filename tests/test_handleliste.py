"""handleliste.py: hva lista faktisk sier — antall, dekning, priser og
totalsum — uten FastAPI, uten kurv-kall mot Oda.

Før lå denne beregningen i web/main.py, og eneste vei inn var å rendre
HTML og lese tallene ut igjen med regex."""

from __future__ import annotations

import pandas as pd
import pytest

from min_oda import blocklist, engangsvarer, representatives
from min_oda.build_list import curate
from min_oda.handleliste import Kilder, Valg, bygg


@pytest.fixture(autouse=True)
def isolert(tmp_path, monkeypatch):
    """Blokkeringer, engangsvarer og valgte representanter leses fra disk —
    pek dem mot tmp så testene ikke ser (eller rører) ekte brukerdata."""
    monkeypatch.setattr(blocklist, "BLOCKLIST_PATH", tmp_path / "blocklist.json")
    monkeypatch.setattr(
        blocklist, "TYPE_BLOCKLIST_PATH", tmp_path / "blocked_types.json"
    )
    monkeypatch.setattr(engangsvarer, "ENGANGS_PATH", tmp_path / "engangsvarer.json")
    monkeypatch.setattr(representatives, "CHOSEN_PATH", tmp_path / "chosen.json")


def _kurv(*varer: tuple[int, str, int]) -> pd.DataFrame:
    """Kurv på formen fetch_cart returnerer: (product_id, varetype, antall)."""
    return pd.DataFrame([
        {"product_id": pid, "product_name": f"vare {pid}", "category": "",
         "quantity": antall, "varetype": varetype}
        for pid, varetype, antall in varer
    ])


def test_ny_liste_gir_en_rad_per_varetype(lines_milk_and_bread, today):
    liste = bygg(lines_milk_and_bread, Valg(new_list=True), today=today)

    assert {r.key for r in liste.rader} == {"melk", "brød"}
    for r in liste.rader:
        assert r.forslag >= 1
        assert r.qty == r.forslag       # ny liste: ingen kurv å trekke fra
        assert r.i_kurv is None
        assert r.mangler is None
        assert r.is_engangs is False


def test_forslag_er_samme_tall_som_curate(lines_milk_and_bread, today):
    """Formelen for foreslått antall sto tidligere både i curate og i
    rad-byggingen. Nå er det ett tall — denne testen holder dem sammen."""
    valg = Valg(cycle=14, new_list=True)
    liste = bygg(lines_milk_and_bread, valg, today=today)
    ideal = curate(lines_milk_and_bread, list_cycle_days=14, today=today)

    fra_curate = dict(zip(ideal["key"], ideal["foreslått_antall"]))
    assert {r.key: r.forslag for r in liste.rader} == fra_curate


def test_kurv_med_annen_variant_dekker_varetypen(lines_substitution, today):
    """Kurven har Q-melk, lista foreslår TINE — samme varetype, så raden
    faller bort. Dekning telles per varetype, ikke per produkt."""
    kurv = _kurv((11, "melk", 5))
    liste = bygg(lines_substitution, Valg(), kurv=kurv, today=today)

    assert [r.key for r in liste.rader] == []
    assert liste.kurv_antall == 5


def test_top_up_viser_varetype_med_for_lavt_antall(lines_substitution, today):
    """Én i kurven, men syklusen krever mer: top_up tar den med, med
    mangler = forslag − i kurv."""
    kurv = _kurv((11, "melk", 1))
    liste = bygg(
        lines_substitution, Valg(cycle=28, top_up=True), kurv=kurv, today=today
    )

    (rad,) = [r for r in liste.rader if r.key == "melk"]
    assert rad.i_kurv == 1
    assert rad.mangler == rad.forslag - 1
    assert rad.qty == rad.mangler


def test_pris_og_bilde_kommer_fra_kilder(lines_milk_and_bread, today):
    liste = bygg(
        lines_milk_and_bread,
        Valg(new_list=True),
        kilder=Kilder(priser={1: 20.0, 2: 35.0}, bilder={1: "http://bilde/melk"}),
        today=today,
    )
    rader = {r.key: r for r in liste.rader}

    assert rader["melk"].unit_price == 20.0
    assert rader["melk"].image == "http://bilde/melk"
    assert rader["melk"].line_cost == 20.0 * rader["melk"].qty
    assert rader["brød"].image is None
    assert liste.total == sum(round(r.line_cost) for r in liste.rader)


def test_rad_utenfor_baseline_markeres_som_ekstra(lines_milk_and_bread, today):
    """is_extra er «denne kom til fordi du utvidet filtrene» — alt utenfor
    baseline-settet."""
    liste = bygg(
        lines_milk_and_bread,
        Valg(new_list=True),
        kilder=Kilder(baseline_ids={1}),
        today=today,
    )
    rader = {r.key: r for r in liste.rader}

    assert rader["melk"].is_extra is False
    assert rader["brød"].is_extra is True
    assert liste.ekstra_antall == 1


def test_engangsvare_telles_per_produkt_ikke_varetype(lines_milk_and_bread, today):
    """En engangsvare er valgt som *den varen* og står utenfor
    varetype-logikken (jf. CONTEXT.md): melk i kurven dekker den ikke."""
    engangsvarer.add(900, name="Kokosmelk boks", price=25.0, qty=2)
    kurv = _kurv((1, "melk", 3))

    liste = bygg(lines_milk_and_bread, Valg(), kurv=kurv, today=today)
    (rad,) = [r for r in liste.rader if r.is_engangs]

    assert rad.i_kurv == 0
    assert rad.mangler == 2
    assert rad.key == "engangs"


def test_engangsvarer_vises_selv_uten_kadens(today):
    """Tom historikk gir ingen kuraterte rader, men engangsvarene skal
    fortsatt med."""
    engangsvarer.add(900, name="Grillkull", price=79.0)
    liste = bygg(pd.DataFrame(columns=[
        "product_id", "product_name", "category", "order_id", "date", "quantity",
    ]), Valg(new_list=True), today=today)

    assert [r.product_name for r in liste.rader] == ["Grillkull"]


def test_sok_filtrerer_paa_navn_og_varetype(lines_milk_and_bread, today):
    liste = bygg(lines_milk_and_bread, Valg(search="solsikke", new_list=True),
                 today=today)
    assert [r.key for r in liste.rader] == ["brød"]


def test_valg_fra_form_leser_hele_filtersettet():
    """HTMX sender filtrene som form-data; ett sted tolker dem."""
    valg = Valg.fra_form({
        "cycle": "14", "top": "10", "max_per_cat": "3",
        "search": "melk", "new_list": "true", "top_up": "on",
    })
    assert valg == Valg(cycle=14, top=10, max_per_cat=3, search="melk",
                        new_list=True, top_up=True)

    tomt = Valg.fra_form({"cycle": "", "top": "tull"})
    assert tomt == Valg()
