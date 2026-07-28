"""Tester for search_products — parsing av Odas katalogsøk (offline)."""

from __future__ import annotations

from min_oda import oda_client


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


PAYLOAD = {
    "items": [
        # Kategorier og kampanjer skal filtreres bort.
        {"type": "category", "attributes": {"id": 1155, "name": "Buksebleier"}},
        {
            "type": "product",
            "attributes": {
                "id": 65006,
                "full_name": "R Lev Vel buksebleier Str. 6, 16-26 kg",
                "gross_price": "21.45",
                "availability": {"is_available": True},
                "images": [
                    {"thumbnail": {"url": "https://img/thumb.jpg"},
                     "large": {"url": "https://img/large.jpg"}}
                ],
            },
        },
        # Utsolgt vare skal filtreres bort.
        {
            "type": "product",
            "attributes": {
                "id": 40657,
                "full_name": "Utsolgt vare",
                "gross_price": "38.70",
                "availability": {"is_available": False},
                "images": [],
            },
        },
    ]
}


def test_search_products_parses_and_filters(monkeypatch):
    monkeypatch.setattr(oda_client.httpx, "get", lambda *a, **k: _Resp(PAYLOAD))
    out = oda_client.search_products("buksebleier")
    assert out == [
        {
            "product_id": 65006,
            "name": "R Lev Vel buksebleier Str. 6, 16-26 kg",
            "price": 21.45,
            "image": "https://img/thumb.jpg",
        }
    ]


def test_search_products_respects_limit(monkeypatch):
    many = {"items": [PAYLOAD["items"][1]] * 5}
    monkeypatch.setattr(oda_client.httpx, "get", lambda *a, **k: _Resp(many))
    assert len(oda_client.search_products("melk", limit=3)) == 3
