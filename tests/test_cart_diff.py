"""Tester for compute_diff — sammenligning av ideell liste mot handlekurv."""

from __future__ import annotations

import pandas as pd

from min_oda.cart_diff import compute_diff


def _ideal(items: list[tuple[str, int]]) -> pd.DataFrame:
    """Ideell liste på minimal form: (key, foreslått_antall)."""
    return pd.DataFrame(
        [{"key": k, "foreslått_antall": q} for k, q in items]
    )


def _cart(items: list[tuple[str, int]]) -> pd.DataFrame:
    """Kurv på minimal form: (_type, quantity)."""
    if not items:
        return pd.DataFrame(columns=["_type", "quantity"])
    return pd.DataFrame(
        [{"_type": t, "quantity": q} for t, q in items]
    )


def test_empty_cart_all_missing():
    ideal = _ideal([("melk", 2), ("brød", 3)])
    cart = _cart([])
    out = compute_diff(ideal, cart, top_up=False)
    assert set(out["key"]) == {"melk", "brød"}
    assert (out["i_kurv"] == 0).all()
    assert list(out["mangler"]) == [2, 3]


def test_substituted_brand_covers_need():
    """Faste varer foreslår TINE Lettmelk; kurv har Q-melk (samme _type).
    Da skal "melk" forsvinne fra diffen — behovet er dekket."""
    ideal = _ideal([("melk", 2), ("brød", 3)])
    cart = _cart([("melk", 2)])
    out = compute_diff(ideal, cart, top_up=False)
    assert "melk" not in set(out["key"])
    assert "brød" in set(out["key"])


def test_top_up_catches_partial_coverage():
    """1 i kurv, 2 foreslått: default skjuler raden (i_kurv > 0), top_up
    viser den med mangler=1."""
    ideal = _ideal([("melk", 2)])
    cart = _cart([("melk", 1)])

    default = compute_diff(ideal, cart, top_up=False)
    assert default.empty

    topup = compute_diff(ideal, cart, top_up=True)
    assert len(topup) == 1
    assert int(topup["i_kurv"].iloc[0]) == 1
    assert int(topup["mangler"].iloc[0]) == 1


def test_overfilled_cart_clips_at_zero():
    """3 i kurv, 2 foreslått: mangler skal ikke gå negativt."""
    ideal = _ideal([("melk", 2)])
    cart = _cart([("melk", 3)])
    topup = compute_diff(ideal, cart, top_up=True)
    assert topup.empty  # mangler == 0, filtreres bort


def test_cart_with_unknown_type_ignored():
    """Kurv-rader uten _type (ukjent varetype) skal ikke krasje."""
    ideal = _ideal([("melk", 2)])
    cart = pd.DataFrame([
        {"_type": "melk", "quantity": 1},
        {"_type": None, "quantity": 5},
    ])
    out = compute_diff(ideal, cart, top_up=True)
    assert int(out["i_kurv"].iloc[0]) == 1
