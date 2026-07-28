"""Tester for engangsvarer: lokal huskeliste for katalogtreff som
postes til Oda først ved bulk."""

from __future__ import annotations

import pytest

from min_oda import engangsvarer


@pytest.fixture
def tmp_engangs(tmp_path, monkeypatch):
    """Pek ENGANGS_PATH mot en tmp-fil."""
    path = tmp_path / "engangsvarer.json"
    monkeypatch.setattr(engangsvarer, "ENGANGS_PATH", path)
    return path


def test_add_list_remove_roundtrip(tmp_engangs):
    engangsvarer.add(100, name="Grillkull 2,5 kg", price=79.9, image="http://x/1.jpg")
    items = engangsvarer.list_items()
    assert len(items) == 1
    assert items[0]["product_id"] == 100
    assert items[0]["name"] == "Grillkull 2,5 kg"
    assert items[0]["price"] == 79.9
    assert items[0]["qty"] == 1

    engangsvarer.remove(100)
    assert engangsvarer.list_items() == []


def test_add_same_pid_oker_antall(tmp_engangs):
    engangsvarer.add(100, name="Grillkull")
    engangsvarer.add(100, name="Grillkull")
    assert engangsvarer.list_items()[0]["qty"] == 2


def test_remove_posted_fjerner_bare_postede(tmp_engangs):
    engangsvarer.add(100, name="Grillkull")
    engangsvarer.add(200, name="Bursdagskake")
    engangsvarer.remove_posted([100, 999])
    items = engangsvarer.list_items()
    assert [i["product_id"] for i in items] == [200]
