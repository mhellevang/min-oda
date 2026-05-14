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
    ("/diff", 307),
    ("/restock", 307),
    ("/reload", 307),
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
