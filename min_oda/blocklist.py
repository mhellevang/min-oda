"""Manuell blokkering av enkeltprodukter fra forslag.

Bruksmønster: appen foreslår et produkt du midlertidig ikke vil ha
(typisk: bleier i størrelse barnet vokste forbi). Du blokkerer det
fra UI; det forsvinner fra forslag inntil du fjerner blokkeringen
igjen — uten å miste produktet fra historikken eller hindre at andre
produkter i samme varetype tar over som representant.

Lagres i `data/blocklist.json`. Filen er gitignored og kan også
redigeres for hånd (f.eks. for å legge til en `note`).
"""

from __future__ import annotations

import json
from datetime import date

from .data_loader import DATA_DIR

BLOCKLIST_PATH = DATA_DIR / "blocklist.json"
# Varetype-blokk holdes i egen fil så produkt-blokk-formatet (rene id-nøkler)
# forblir uendret. Blokkeres en varetype forsvinner hele typen fra forslag,
# ikke bare én variant — jf. `blocked_types` i build_list.curate.
TYPE_BLOCKLIST_PATH = DATA_DIR / "blocked_types.json"


def _load_from(path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_to(path, data: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _load_raw() -> dict[str, dict]:
    return _load_from(BLOCKLIST_PATH)


def _save_raw(data: dict[str, dict]) -> None:
    _save_to(BLOCKLIST_PATH, data)


def blocked_ids() -> set[int]:
    """Produkt-id-er som er blokkert akkurat nå."""
    out: set[int] = set()
    for k in _load_raw().keys():
        try:
            out.add(int(k))
        except (TypeError, ValueError):
            continue
    return out


def list_blocked() -> list[dict]:
    """Blokkerte varer for visning, nyeste først."""
    items: list[dict] = []
    for pid, info in _load_raw().items():
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            continue
        info = info if isinstance(info, dict) else {}
        items.append({"product_id": pid_int, **info})
    return sorted(items, key=lambda r: r.get("added", ""), reverse=True)


def block(product_id: int, name: str = "", note: str = "") -> None:
    """Legg til (eller oppdater) en blokkert vare. `note` er valgfri og
    skrives bare hvis den faktisk er satt — sånn at manuelt redigerte
    notater ikke overskrives når UI re-blokkerer samme produkt."""
    raw = _load_raw()
    key = str(int(product_id))
    existing = raw.get(key, {}) if isinstance(raw.get(key), dict) else {}
    entry = {
        "name": name or existing.get("name", ""),
        "added": existing.get("added") or date.today().isoformat(),
    }
    new_note = note or existing.get("note", "")
    if new_note:
        entry["note"] = new_note
    raw[key] = entry
    _save_raw(raw)


def unblock(product_id: int) -> None:
    raw = _load_raw()
    if raw.pop(str(int(product_id)), None) is not None:
        _save_raw(raw)


# ---------- varetype-blokk ------------------------------------------------


def blocked_types() -> set[str]:
    """Varetyper (curate-nøkler) som er blokkert akkurat nå."""
    return {str(k) for k in _load_from(TYPE_BLOCKLIST_PATH).keys()}


def list_blocked_types() -> list[dict]:
    """Blokkerte varetyper for visning, nyeste først."""
    items: list[dict] = []
    for key, info in _load_from(TYPE_BLOCKLIST_PATH).items():
        info = info if isinstance(info, dict) else {}
        items.append({"key": str(key), **info})
    return sorted(items, key=lambda r: r.get("added", ""), reverse=True)


def block_type(key: str, name: str = "") -> None:
    """Blokker en hel varetype. `name` er en valgfri visningsetikett
    (typisk samme som key med stor forbokstav)."""
    key = str(key).strip()
    if not key:
        return
    raw = _load_from(TYPE_BLOCKLIST_PATH)
    existing = raw.get(key, {}) if isinstance(raw.get(key), dict) else {}
    raw[key] = {
        "name": name or existing.get("name", ""),
        "added": existing.get("added") or date.today().isoformat(),
    }
    _save_to(TYPE_BLOCKLIST_PATH, raw)


def unblock_type(key: str) -> None:
    raw = _load_from(TYPE_BLOCKLIST_PATH)
    if raw.pop(str(key), None) is not None:
        _save_to(TYPE_BLOCKLIST_PATH, raw)
