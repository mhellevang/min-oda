"""Engangsvarer fra katalogsøket.

Søketreff som ikke hører hjemme i kadenslogikken (bursdagskake, grillkull)
legges her som en lokal huskeliste. De rendres som rader i handlelista og
blir med i neste bulk-post til Oda, og fjernes herfra når de er postet.
Ingenting sendes til Oda før brukeren trykker på knappen.

Lagres i `data/engangsvarer.json` (gitignored). Varen finnes ikke i
CSV-ene, så et snapshot (navn, pris, bilde) fra søketreffet lagres med.
"""

from __future__ import annotations

from datetime import date

from .filer import DATA_DIR, les, skriv

ENGANGS_PATH = DATA_DIR / "engangsvarer.json"


def list_items() -> list[dict]:
    """Alle engangsvarer, eldste først.
    Hver: product_id, name, price, image, qty, added."""
    items = []
    for pid, info in les(ENGANGS_PATH).items():
        if not isinstance(info, dict):
            continue
        items.append({
            "product_id": int(pid),
            "name": str(info.get("name") or ""),
            "price": info.get("price"),
            "image": str(info.get("image") or ""),
            "qty": int(info.get("qty") or 1),
            "added": str(info.get("added") or ""),
        })
    items.sort(key=lambda x: x["added"])
    return items


def add(product_id: int, name: str = "", price: float | None = None,
        image: str = "", qty: int = 1) -> None:
    """Legg til (eller oppdater) en engangsvare. Finnes den fra før,
    økes antallet."""
    raw = les(ENGANGS_PATH)
    key = str(int(product_id))
    if key in raw and isinstance(raw[key], dict):
        raw[key]["qty"] = int(raw[key].get("qty") or 1) + qty
    else:
        raw[key] = {
            "name": name,
            "price": price,
            "image": image,
            "qty": qty,
            "added": date.today().isoformat(),
        }
    skriv(ENGANGS_PATH, raw)


def remove(product_id: int) -> None:
    raw = les(ENGANGS_PATH)
    if raw.pop(str(int(product_id)), None) is not None:
        skriv(ENGANGS_PATH, raw)


def remove_posted(product_ids) -> None:
    """Fjern engangsvarer som nettopp ble postet til Oda."""
    raw = les(ENGANGS_PATH)
    keys = {str(int(pid)) for pid in product_ids}
    remaining = {k: v for k, v in raw.items() if k not in keys}
    if len(remaining) != len(raw):
        skriv(ENGANGS_PATH, remaining)
