"""Valgt representant per varetype.

Bruksmønster: handlelista foreslår den mest kjøpte varianten av en
varetype, men brukeren vil bytte til en katalogvare som aldri er kjøpt
(f.eks. buksebleier i stedet for vanlige bleier). Valget lagres her og
overstyrer mest-kjøpt-logikken i `curate()` til det fjernes. Samtidig
pinnes produktets varetype-klassifisering (jf. `pinned_types`), så
fremtidige kjøp teller i riktig kadens.

Lagres i `data/chosen_products.json` (gitignored). Katalogvaren finnes
ikke i CSV-ene, så et snapshot (navn, pris, bilde) fra søketreffet
lagres sammen med valget — prisen kan bli foreldet.
"""

from __future__ import annotations

from datetime import date

from .filer import DATA_DIR, les, skriv

CHOSEN_PATH = DATA_DIR / "chosen_products.json"

# (mtime_ns, mapping) — pinned_types kalles per rad i df.apply, så les
# fila bare når den faktisk er endret.
_PINNED_CACHE: tuple[int, dict[int, str]] | None = None


def chosen_representatives() -> dict[str, dict]:
    """Varetype-nøkkel → snapshot av valgt produkt
    (product_id, name, price, image, added)."""
    out: dict[str, dict] = {}
    for key, info in les(CHOSEN_PATH).items():
        if isinstance(info, dict) and info.get("product_id"):
            out[str(key)] = info
    return out


def pinned_types() -> dict[int, str]:
    """Produkt-id → varetype-nøkkel. Konsulteres av `product_type()` så
    et valgt produkt klassifiseres til varetypen det ble valgt for."""
    global _PINNED_CACHE
    mtime = CHOSEN_PATH.stat().st_mtime_ns if CHOSEN_PATH.exists() else 0
    if _PINNED_CACHE is None or _PINNED_CACHE[0] != mtime:
        _PINNED_CACHE = (
            mtime,
            {int(info["product_id"]): key
             for key, info in chosen_representatives().items()},
        )
    return _PINNED_CACHE[1]


def choose(key: str, product_id: int, name: str = "",
           price: float | None = None, image: str = "") -> None:
    """Sett (eller bytt) valgt representant for en varetype."""
    raw = les(CHOSEN_PATH)
    raw[str(key)] = {
        "product_id": int(product_id),
        "name": name,
        "price": price,
        "image": image,
        "added": date.today().isoformat(),
    }
    skriv(CHOSEN_PATH, raw)


def unchoose(key: str) -> None:
    raw = les(CHOSEN_PATH)
    if raw.pop(str(key), None) is not None:
        skriv(CHOSEN_PATH, raw)
