"""Smoke-test for web-appens ruter. Sjekker at templates rendrer og at
ruter returnerer 2xx/3xx — fanger template- og rute-regressjoner uten å
gå inn i forretningslogikken (den dekkes av de andre testene).

Krever at data/orders.csv og data/lines.csv finnes; hopper over hvis de
mangler (typisk på CI uten kjørt fetch_orders).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from min_oda.data_loader import DATA_DIR

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "orders.csv").exists() or not (DATA_DIR / "lines.csv").exists(),
    reason="data/orders.csv eller data/lines.csv mangler — kjør fetch_orders først",
)


@pytest.fixture
def client():
    # Importer etter at skip-sjekken har gått, slik at en tom data-mappe
    # ikke krasjer på import.
    from min_oda.web.main import app, invalidate_caches

    invalidate_caches()
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("path,expected", [
    ("/", 307),
    ("/handleliste", 200),
    ("/handleliste?cycle=7&top=20", 200),
    ("/handleliste?new_list=true", 200),
    ("/handleliste/table", 200),
    ("/innsikt", 200),
    ("/innsikt/basket-lookup?q=melk", 200),
])
def test_route_returns_expected_status(client, path, expected):
    r = client.get(path, follow_redirects=False)
    assert r.status_code == expected


def test_handleliste_renders_filter_output(client):
    """Filtrene format_due / status_class skal faktisk produsere output."""
    r = client.get("/handleliste")
    assert r.status_code == 200
    body = r.text
    assert "status-" in body  # status_class filter
    assert any(s in body for s in ("om ", "i dag", "siden"))  # format_due filter


def test_innsikt_renders_kpis(client):
    """/innsikt skal i det minste vise KPI-strukturen."""
    r = client.get("/innsikt")
    assert r.status_code == 200
    assert "Innsikt" in r.text or "innsikt" in r.text.lower()


@pytest.fixture
def isolated_blocklist(tmp_path, monkeypatch):
    """Pek block-fil mot tmp så ekte data/blocklist.json ikke berøres."""
    from min_oda import blocklist
    monkeypatch.setattr(blocklist, "BLOCKLIST_PATH", tmp_path / "blocklist.json")
    yield


def test_block_and_unblock_round_trip(client, isolated_blocklist):
    """POST /handleliste/block legger til; POST /handleliste/unblock
    fjerner. Begge skal returnere det kombinerte body-fragmentet."""
    from min_oda import blocklist

    r = client.post(
        "/handleliste/block",
        data={"product_id": "999999", "name": "Testvare X"},
    )
    assert r.status_code == 200
    assert "Skjulte varer" in r.text
    assert "Testvare X" in r.text
    assert 999999 in blocklist.blocked_ids()

    r = client.post(
        "/handleliste/unblock",
        data={"product_id": "999999"},
    )
    assert r.status_code == 200
    assert 999999 not in blocklist.blocked_ids()


def test_block_invalid_id_returns_400(client, isolated_blocklist):
    r = client.post("/handleliste/block", data={"product_id": "ikke-tall"})
    assert r.status_code == 400


def _parse_select(html: str, index: int = 0) -> dict:
    """Plukk ut produkt-select nr. `index` fra HTML: navn, hx-vals,
    valgt pid og alle option-pids."""
    import json
    import re

    selects = re.findall(r'<select class="product-select".*?</select>', html, re.S)
    sel = selects[index]
    options = re.findall(r'<option value="(\d+)"\s*(selected)?\s*>', sel)
    return {
        "name": re.search(r'name="(product_select_\d+)"', sel).group(1),
        "vals": json.loads(re.search(r"hx-vals='(\{.*?\})'", sel).group(1)),
        "selected": [p for p, s in options if s],
        "pids": [p for p, _ in options],
    }


def test_variant_swap_uses_row_pid_not_first_select_in_form(client):
    """htmx legger ved hele skjemaet på POST, så swap-endepunktet må slå
    opp `product_select_<pid>` for raden som ble endret, ikke ta det
    første select-feltet i formen (regresjon: dropdownen så ut til å
    resette seg for alle rader unntatt den første)."""
    html = client.get("/handleliste?new_list=true").text
    first = _parse_select(html, 0)
    second = _parse_select(html, 1)
    new_pid = next(p for p in second["pids"] if p not in second["selected"])

    r = client.post(
        "/handleliste/variant-swap",
        data={
            **second["vals"],
            # Første rads select kommer foran i form-serialiseringen.
            first["name"]: first["selected"][0],
            second["name"]: new_pid,
        },
    )
    assert r.status_code == 200
    swapped = _parse_select(r.text)
    assert swapped["selected"] == [new_pid]
    assert swapped["vals"]["pid"] == new_pid
