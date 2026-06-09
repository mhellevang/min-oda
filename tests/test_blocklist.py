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
    """Bleier i to størrelser — en parallell husholdning med treåring i str. 6
    og spedbarn i str. 5. Klassifiseres som to varetyper: bleier-str5 og
    bleier-str6, hver med egen rytme og representant."""
    rows = []
    for i, d in enumerate([60, 45, 30, 15]):
        rows.append(_line(500, "Libero Bleier Comfort Str. 5", "Bleier", f"s5-{i}", d))
    for i, d in enumerate([12, 8, 4, 1]):
        rows.append(_line(600, "Libero Bleier Comfort Str. 6", "Bleier", f"s6-{i}", d))
    return pd.DataFrame(rows)


@pytest.fixture
def lines_two_brands_same_size() -> pd.DataFrame:
    """To merker av bleier i samme størrelse — skal slås sammen til én
    bleier-str3-varetype. Blokk av ett merke skal la det andre overta."""
    rows = []
    for i, d in enumerate([60, 45, 30, 15]):
        rows.append(_line(700, "Libero Touch bleie Str. 3, 5-9 kg", "Bleier", f"l3-{i}", d))
    for i, d in enumerate([55, 40, 25, 10]):
        rows.append(_line(701, "Pampers Baby-Dry bleie Str. 3, 6-10kg", "Bleier", f"p3-{i}", d))
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


def test_curate_keeps_parallel_sizes(lines_two_diaper_sizes):
    """Ulike størrelser av bleier er separate varetyper og skal begge med
    på listen — det er hele poenget med size-suffikset."""
    out = curate(lines_two_diaper_sizes, max_per_category=5, top_n=10, today=TODAY)
    keys = set(out["key"])
    assert "bleier-str5" in keys
    assert "bleier-str6" in keys


def test_curate_blocking_one_size_keeps_the_other(lines_two_diaper_sizes):
    """Brukeren blokkerer str. 5 (utvokst). Str. 6 skal fortsatt bli foreslått
    som sin egen rad."""
    out = curate(
        lines_two_diaper_sizes, max_per_category=5, top_n=10, blocked={500},
        today=TODAY,
    )
    keys = set(out["key"])
    assert "bleier-str5" not in keys
    assert "bleier-str6" in keys


def test_curate_substitution_within_size_when_brand_blocked(lines_two_brands_same_size):
    """Brand A og brand B i samme str. 3 → én varetype 'bleier-str3'.
    Blokk av brand A skal la brand B overta som representant — substitusjonen
    overlever på størrelsesnivå."""
    out_unblocked = curate(
        lines_two_brands_same_size, max_per_category=5, top_n=10, today=TODAY,
    )
    bleier = out_unblocked[out_unblocked["key"] == "bleier-str3"]
    assert len(bleier) == 1
    assert "Libero" in str(bleier["product_name"].iloc[0])

    out_blocked = curate(
        lines_two_brands_same_size, max_per_category=5, top_n=10, blocked={700},
        today=TODAY,
    )
    bleier = out_blocked[out_blocked["key"] == "bleier-str3"]
    assert len(bleier) == 1
    assert "Pampers" in str(bleier["product_name"].iloc[0])


def test_curate_drops_type_when_all_variants_blocked(lines_two_diaper_sizes):
    """Hvis alle produkter i en varetype er blokkert, faller varetypen
    helt ut av forslagene. Med size-suffiks gjelder dette per størrelse —
    blokker man begge størrelsene forsvinner begge."""
    out = curate(
        lines_two_diaper_sizes, max_per_category=5, top_n=10,
        blocked={500, 600}, today=TODAY,
    )
    keys = set(out["key"])
    assert "bleier-str5" not in keys
    assert "bleier-str6" not in keys


def test_curate_default_blocked_is_empty(lines_two_diaper_sizes):
    """Uten `blocked`-parameter skal curate oppføre seg som før."""
    out = curate(lines_two_diaper_sizes, max_per_category=5, top_n=10, today=TODAY)
    assert not out.empty
    keys = set(out["key"])
    assert "bleier-str5" in keys
    assert "bleier-str6" in keys
