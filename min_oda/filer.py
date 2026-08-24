"""Hvor dataene ligger, og lesing/skriving av de små JSON-filene.

`DATA_DIR` var deklarert i fem moduler med samme uttrykk; her er den ett
sted. `les`/`skriv` er formatet blokkeringer, engangsvarer og valgte
representanter deler — to av dem importerte tidligere
`blocklist._load_from`/`_save_to`, altså en privat detalj i en tredje
modul.

En fil som mangler eller er ødelagt leses som tomt oppslag: disse filene
er brukerens egne, gitignored, og kan redigeres for hånd.
"""

from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


def les(path: Path) -> dict[str, dict]:
    """Innholdet i en JSON-fil, eller {} hvis den mangler eller er ødelagt."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def skriv(path: Path, data: dict[str, dict]) -> None:
    """Skriv oppslaget som lesbar JSON (filene redigeres for hånd)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
