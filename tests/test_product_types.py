"""Tester for product_type — klassifisering av Oda-produkter til varetype."""

from __future__ import annotations

from min_oda.product_types import product_type


def test_keyword_basic():
    """Vanlig navn matches via keyword-regel."""
    assert product_type("TINE Lettmelk 1 L", "Meieri") == "melk"
    assert product_type("Korn Bakeri Solsikkebrød 620 g", "Bakeri") == "brød"


def test_specific_rule_wins_over_general():
    """Gresk yoghurt skal klassifiseres som yoghurt-gresk, ikke yoghurt.
    Regelen står over den generelle i _KEYWORD_RULES."""
    assert product_type("TINE Gresk Yoghurt Naturell", "Meieri") == "yoghurt-gresk"
    assert product_type("Junior Yoghurt Banan", "Yoghurt") == "yoghurt-junior"
    # Vanlig yoghurt fortsatt yoghurt.
    assert product_type("TINE Yoghurt Skogsbær", "Meieri") == "yoghurt"


def test_baby_rule_before_general():
    """Bleier/babymat skal klassifiseres riktig før mer generelle regler."""
    assert product_type("Libero Bleier Comfort Str. 3", "Bleier") == "bleier"
    assert product_type("Hipp Babymat Frukt 4 mnd", "Baby") == "babymat"


def test_pizzadeig_before_brød():
    """Pizzabunn skal ikke klassifiseres som brød selv om det er bakeri."""
    assert product_type("Folkets Pizzadeig", "Bakeri") == "pizzadeig"


def test_category_fallback_when_no_keyword_match():
    """Et produkt uten keyword-treff faller tilbake på kategori-fallbacken."""
    assert product_type("Et ukjent eksotisk produkt", "Frukt og grønt") == "frukt-grønt-annet"
    assert product_type("Noe uklassifisert", "Sjokolade, snacks og godteri") == "snacks"


def test_returns_none_when_nothing_matches():
    """Verken keyword eller kjent kategori → None."""
    assert product_type("Et helt rart produktnavn", "Ukjent kategori") is None
    assert product_type(None, None) is None


def test_handles_empty_name():
    """Tomt navn skal ikke krasje — vi faller bare gjennom til kategori-fallback."""
    assert product_type("", "Frukt og grønt") == "frukt-grønt-annet"
    assert product_type("", "Ukjent") is None
