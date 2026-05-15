"""Tester for blokk-mekanismen: lagring, curate-integrasjon, kant-tilfeller."""

from __future__ import annotations

import pandas as pd
import pytest

from min_oda import blocklist
from min_oda.build_list import curate

TODAY = pd.Timestamp("2026-05-14", tz="UTC")


def _line(pid, name, cat, oid, days_ago):
    return {
        "product_id": pid, "product_name": name, "category": cat,
        "order_id": oid, "date": TODAY - pd.Timedelta(days=days_ago),
        "quantity": 1, "line_total": 25.0,
    }


@pytest.fixture
def tmp_blocklist(tmp_path, monkeypatch):
    """Pek BLOCKLIST_PATH mot en tmp-fil så tester ikke rører ekte data."""
    path = tmp_path / "blocklist.json"
    monkeypatch.setattr(blocklist, "BLOCKLIST_PATH", path)
    return path


@pytest.fixture
def lines_two_diaper_sizes() -> pd.DataFrame:
    """Bleier i størrelse 5 (mange historiske kjøp) og en ny som
    nettopp er begynt på størrelse 6. Begge klassifiseres som "bleier"."""
    rows = []
    # Str. 5 — fast vare gjennom hele historikken.
    for i, d in enumerate([60, 45, 30, 15]):
        rows.append(_line(500, "Libero Bleier Comfort Str. 5", "Bleier", f"s5-{i}", d))
    # Str. 6 — nytt, men nok ordrer til at den ikke filtreres ut av min_buys.
    for i, d in enumerate([12, 8, 4, 1]):
        rows.append(_line(600, "Libero Bleier Comfort Str. 6", "Bleier", f"s6-{i}", d))
    return pd.DataFrame(rows)


def test_block_persists_round_trip(tmp_blocklist):
    """Block → list_blocked → unblock — alt skal henge sammen."""
    blocklist.block(12345, name="Test-vare")
    assert blocklist.blocked_ids() == {12345}

    items = blocklist.list_blocked()
    assert len(items) == 1
    assert items[0]["product_id"] == 12345
    assert items[0]["name"] == "Test-vare"
    assert "added" in items[0]

    blocklist.unblock(12345)
    assert blocklist.blocked_ids() == set()
    assert blocklist.list_blocked() == []


def test_blocklist_empty_when_file_missing(tmp_blocklist):
    """Ingen fil = tomt sett, ikke krasj."""
    assert not tmp_blocklist.exists()
    assert blocklist.blocked_ids() == set()
    assert blocklist.list_blocked() == []


def test_block_preserves_existing_note(tmp_blocklist):
    """Manuelt redigert `note` overlever en re-blokk fra UI (som ikke
    sender note-felt). Viktig fordi UI ikke har note-input."""
    blocklist.block(42, name="Pampers")
    # Simuler at brukeren har redigert filen for å legge til note
    raw = blocklist._load_raw()
    raw["42"]["note"] = "vokst forbi"
    blocklist._save_raw(raw)

    # UI re-blokkerer samme produkt uten note
    blocklist.block(42, name="Pampers")
    items = blocklist.list_blocked()
    assert items[0]["note"] == "vokst forbi"


def test_curate_filters_blocked_product(lines_two_diaper_sizes):
    """Når str. 5 blokkeres, skal str. 6 ta over som representant for
    "bleier"-varetypen — ikke at hele varetypen forsvinner."""
    out_unblocked = curate(lines_two_diaper_sizes, max_per_category=5, top_n=10)
    # Uten blokk: str. 5 vinner (flest distinkte ordrer)
    bleier = out_unblocked[out_unblocked["key"] == "bleier"]
    assert len(bleier) == 1
    assert "Str. 5" in str(bleier["product_name"].iloc[0])

    # Med blokk: str. 6 tar over
    out_blocked = curate(
        lines_two_diaper_sizes, max_per_category=5, top_n=10, blocked={500},
    )
    bleier = out_blocked[out_blocked["key"] == "bleier"]
    assert len(bleier) == 1
    assert "Str. 6" in str(bleier["product_name"].iloc[0])


def test_curate_drops_type_when_all_variants_blocked(lines_two_diaper_sizes):
    """Hvis alle produkter i varetypen er blokkert, faller varetypen
    helt ut av forslagene."""
    out = curate(
        lines_two_diaper_sizes, max_per_category=5, top_n=10,
        blocked={500, 600},
    )
    assert (out["key"] == "bleier").sum() == 0


def test_curate_default_blocked_is_empty(lines_two_diaper_sizes):
    """Uten `blocked`-parameter skal curate oppføre seg som før."""
    out = curate(lines_two_diaper_sizes, max_per_category=5, top_n=10)
    assert not out.empty
    assert "bleier" in out["key"].tolist()
