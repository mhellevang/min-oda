"""Tester for product_type — klassifisering av Oda-produkter til varetype."""

from __future__ import annotations

from min_oda.product_types import product_type


def test_keyword_basic():
    """Vanlig navn matches via keyword-regel."""
    assert product_type("TINE Lettmelk 1 L", "Meieri") == "melk"
    assert product_type("Korn Bakeri Solsikkebrød 620 g", "Bakeri") == "brød"


def test_specific_rule_wins_over_general():
    """Gresk/tyrkisk yoghurt skal klassifiseres som yoghurt-gresk-tyrkisk,
    ikke yoghurt. Regelen står over den generelle i _KEYWORD_RULES."""
    assert product_type("TINE Gresk Yoghurt Naturell", "Meieri") == "yoghurt-gresk-tyrkisk"
    assert product_type("Salakis Yoghurt Tyrkisk Naturell 10%", "Meieri") == "yoghurt-gresk-tyrkisk"
    assert product_type("Junior Yoghurt Banan", "Yoghurt") == "yoghurt-junior"
    # Vanlig yoghurt fortsatt yoghurt.
    assert product_type("TINE Yoghurt Skogsbær", "Meieri") == "yoghurt"


def test_baby_rule_before_general():
    """Bleier/babymat skal klassifiseres riktig før mer generelle regler.
    Størrelses-suffikset (str3, 4mnd) skiller subtyper innen samme varetype."""
    assert product_type("Libero Bleier Comfort Str. 3", "Bleier") == "bleier-str3"
    assert product_type("Hipp Babymat Frukt 4 mnd", "Baby") == "babymat-4mnd"


def test_size_suffix_splits_diaper_sizes():
    """Bleier i ulike størrelser skal få ulike varetype-keys så de ikke
    konkurrerer om samme slot i handlelisten (treåring str. 6 + tvillinger
    str. 3 må kunne eksistere parallelt)."""
    assert product_type("Libero Touch bleie Str. 3, 5-9 kg", "Bleier") == "bleier-str3"
    assert product_type("R Lev Vel bleier XL Str. 6, 15-30kg", "Bleier") == "bleier-str6"
    # Str. dominerer over kg-rangen — vi vil ikke ha "bleier-15-30kg".
    assert "kg" not in product_type("R Lev Vel bleier XL Str. 6, 15-30kg, 22 stk", "Bleier")


def test_size_suffix_collapses_brands_within_same_size():
    """Samme størrelse, forskjellig merke → samme varetype. Substitusjonen
    skjer fortsatt innen et størrelsesnivå (Libero og Pampers str. 3 er
    fortsatt utbyttbare for samme barn)."""
    libero = product_type("Libero Touch bleie Str. 3, 5-9 kg", "Bleier")
    pampers = product_type("Pampers Baby-Dry bleie Str. 3, 6-10kg", "Bleier")
    assert libero == pampers == "bleier-str3"


def test_size_suffix_from_age_label():
    """Babymat og morsmelkerstatning bruker alder ('Fra N mnd', 'N mnd') —
    NAN 1 (0 mnd) og NAN 2 (6 mnd) er genuint forskjellige stadier."""
    assert product_type("Grogro Måltid Fra 6 mnd, 100 g", "Babymat") == "babymat-6mnd"
    assert product_type("Grogro Pulvergrøt Fra 12 mnd, 300 g", "Babymat") == "babymat-12mnd"
    assert product_type("Nestlé NAN Pro 1 fra 0 mnd, 1200 ml", "Baby") == "morsmelkerstatning-0mnd"


def test_no_size_suffix_when_name_lacks_size_code():
    """Vanlige varer uten størrelses-kode skal være uberørt."""
    assert product_type("TINE Lettmelk 1 L", "Meieri") == "melk"
    assert product_type("Korn Bakeri Solsikkebrød 620 g", "Bakeri") == "brød"


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


def test_buksebleier_er_bleier():
    """«buksebleier» mangler ordgrense foran «bleie» — regexen skal
    likevel treffe, med størrelses-suffiks som vanlig."""
    assert product_type("R Lev Vel buksebleier Str. 6, 16-26 kg", "Baby og barn") == "bleier-str6"
    assert product_type("Libero Touch buksebleier Str. 5, 10-14 kg", "Bleier") == "bleier-str5"
