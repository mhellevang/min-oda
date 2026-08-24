"""Cachede oppslag for web-laget, med avhengighetene skrevet ned.

Alt her er dyrt å regne og billig å gjenbruke innen samme datasett. Det
som gjør modulen verdt å ha, er ikke memoiseringen men `_AVLEDET_AV`:
kartet over hva som må regnes på nytt når noe endrer seg. Rutene sier
hva som skjedde (`endret(BLOKKERING)`), ikke hvilke variabler som skal
nullstilles — det var kunnskap som før bare fantes i hver rutes kropp.

Kurven har sin egen levetid (`_KURV_TTL`) siden den kan endres fra Oda
sine egne sider mens appen står åpen.
"""

from __future__ import annotations

from time import time
from typing import Any, Callable

import pandas as pd

from .. import blocklist, innsikt, representatives
from ..build_list import curate
from ..cart_diff import fetch_cart
from ..data_loader import load_both
from ..handleliste import (
    DEFAULT_CYCLE,
    DEFAULT_MAX_PER_CAT,
    DEFAULT_TOP,
    EMPTY_CART,
    Kilder,
)
from ..oda_client import MissingCredentials, build_client
from ..restock import compute_cadence
from ..siste_kjop import siste_bilder, siste_priser

# Hva som kan endre seg under appen.
DATA = "data"                  # ny fetch fra Oda
KURV = "kurv"                  # vi la noe i kurven
BLOKKERING = "blokkering"      # blokk/avblokk av produkt eller varetype
REPRESENTANT = "representant"  # valgt/fjernet representant for en varetype

# Hva som er avledet av hver kilde, og dermed må regnes på nytt.
# Kadensen står under REPRESENTANT fordi pinningen kan omklassifisere et
# produkt som finnes i historikken.
_AVLEDET_AV: dict[str, set[str]] = {
    DATA: {"data", "kurv", "basket", "kadens", "priser", "bilder", "baseline_ids"},
    KURV: {"kurv"},
    BLOKKERING: {"baseline_ids"},
    REPRESENTANT: {"baseline_ids", "kadens"},
}

_KURV_TTL = 120.0

_cache: dict[str, Any] = {}
_kurv_hentet = 0.0


def endret(kilde: str) -> None:
    """Meld at noe har endret seg, så det som er avledet av det regnes på
    nytt ved neste oppslag."""
    global _kurv_hentet
    if kilde not in _AVLEDET_AV:
        raise ValueError(f"Ukjent kilde: {kilde}")
    for navn in _AVLEDET_AV[kilde]:
        _cache.pop(navn, None)
    if "kurv" in _AVLEDET_AV[kilde]:
        _kurv_hentet = 0.0


def _husk(navn: str, beregn: Callable[[], Any]) -> Any:
    if navn not in _cache:
        _cache[navn] = beregn()
    return _cache[navn]


def orders_og_lines() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Orders + lines med date joined. Grunnlaget for alt annet her."""
    return _husk("data", load_both)


def lines() -> pd.DataFrame:
    return orders_og_lines()[1]


def kurv() -> pd.DataFrame:
    """Handlekurven på Oda. Uten cookies returneres en tom kurv så siden
    fortsatt rendrer — auth-banneret forteller brukeren hva som er galt."""
    global _kurv_hentet
    if "kurv" not in _cache or (time() - _kurv_hentet) > _KURV_TTL:
        try:
            _cache["kurv"] = fetch_cart(build_client())
        except MissingCredentials:
            _cache["kurv"] = EMPTY_CART
        _kurv_hentet = time()
    return _cache["kurv"]


def basket_par() -> tuple:
    """Basket-parene til /innsikt. O(n²) i antall vanlige produkter, så
    regn dem én gang per datasett."""
    return _husk("basket", lambda: innsikt.basket_pairs(lines()))


def kadens() -> pd.DataFrame:
    """compute_cadence(by_type=True) — brukt av variant-endepunktene."""
    return _husk("kadens", lambda: compute_cadence(lines(), by_type=True))


def priser() -> dict[int, float]:
    """Sist betalt enhetspris per produkt."""
    return _husk("priser", lambda: siste_priser(lines()))


def bilder() -> dict[int, str]:
    """Bilde-URL per produkt, fra siste kjøp."""
    return _husk("bilder", lambda: siste_bilder(lines()))


def baseline_ids() -> set[int]:
    """Produkt-id-ene som er med ved default-filtre — grunnlaget for å
    markere rader som *kommer til* når brukeren utvider filtrene."""
    def beregn() -> set[int]:
        baseline = curate(
            lines(),
            list_cycle_days=DEFAULT_CYCLE,
            top_n=DEFAULT_TOP,
            max_per_category=DEFAULT_MAX_PER_CAT,
            blocked=blocklist.blocked_ids(),
            blocked_types=blocklist.blocked_types(),
            chosen=representatives.chosen_representatives(),
        )
        return set() if baseline.empty else {int(x) for x in baseline["product_id"]}

    return _husk("baseline_ids", beregn)


def kilder() -> Kilder:
    """De cachede oppslagene handleliste.bygg() trenger."""
    return Kilder(priser=priser(), bilder=bilder(), baseline_ids=baseline_ids())
