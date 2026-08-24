"""Livsløpet til en LLM-generering som tar minutter.

Delt av forslag.py (handleliste) og innsikt_llm.py (innsikt). Alt som er
likt for de to ligger her: single-flight bakgrunnstråd, cache-fila,
ferskhets-sjekken, stemplingen med tidspunkt og provider, og
template-konteksten polling-fragmentene rendres med.

Det som varierer er bare hva som genereres og hvordan svaret valideres
mot virkeligheten — det bor i de to modulene.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import llm


class Generering:
    """Én navngitt generering med egen cache-fil.

    `start(bygg)` kjører `bygg()` i en daemon-tråd; en 'feil'-nøkkel i
    returverdien (eller et unntak) registreres som siste feil. Bare én
    kjøring om gangen — nye kall returnerer None mens den første pågår.
    """

    def __init__(self, navn: str, fil: Path, fersk_timer: float = 24.0):
        self.navn = navn
        self.fil = fil
        self.fersk_timer = fersk_timer
        self._lock = threading.Lock()
        self._kjorer = False
        self._siste_feil: str | None = None

    # --- status ---

    def er_i_gang(self) -> bool:
        with self._lock:
            return self._kjorer

    def siste_feil(self) -> str | None:
        """Feilmelding fra forrige kjøring, None hvis den lyktes."""
        with self._lock:
            return self._siste_feil

    # --- cache ---

    def last(self) -> dict | None:
        """Sist lagrede generering, eller None hvis den aldri har kjørt."""
        if not self.fil.exists():
            return None
        try:
            return json.loads(self.fil.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def er_ferskt(self, max_age_hours: float | None = None) -> bool:
        lagret = self.last()
        if not lagret or not lagret.get("generert"):
            return False
        try:
            alder = datetime.now() - datetime.fromisoformat(lagret["generert"])
        except ValueError:
            return False
        grense = self.fersk_timer if max_age_hours is None else max_age_hours
        return alder.total_seconds() < grense * 3600

    def lagre(self, innhold: dict) -> dict:
        """Stemple med tidspunkt og provider, og skrive til cache-fila.
        Kalles bare når genereringen lyktes — ved feil blir forrige
        generering stående, og UI-et viser den under feilmeldingen."""
        lagret = {
            "generert": datetime.now().isoformat(timespec="minutes"),
            "provider": llm.provider_label(),
            **innhold,
        }
        self.fil.parent.mkdir(parents=True, exist_ok=True)
        self.fil.write_text(json.dumps(lagret, ensure_ascii=False, indent=1))
        return lagret

    # --- kjøring ---

    def start(self, bygg: Callable[[], dict]) -> threading.Thread | None:
        """Kjør bygg() i bakgrunnen. None hvis en kjøring alt pågår."""
        with self._lock:
            if self._kjorer:
                return None
            self._kjorer = True
            self._siste_feil = None

        def _kjor() -> None:
            feil: str | None = None
            try:
                resultat = bygg()
                if isinstance(resultat, dict):
                    feil = resultat.get("feil")
            except Exception as e:  # en trådkrasj må aldri låse jobben
                feil = f"Genereringen feilet: {e}"
            finally:
                with self._lock:
                    self._siste_feil = feil
                    self._kjorer = False

        t = threading.Thread(target=_kjor, daemon=True, name=self.navn)
        t.start()
        return t

    # --- UI ---

    def kontekst(
        self, prefiks: str, start_hvis_gammel: Callable[[], None] | None = None
    ) -> dict:
        """Template-kontekst for polling-fragmentet: `<prefiks>`,
        `<prefiks>_kjorer` og `<prefiks>_feil`.

        `start_hvis_gammel` sendes med på sidelast: da sparkes en
        generering i gang når cachen er for gammel, så den typisk er
        ferdig innen brukeren har jobbet seg gjennom siden. Uten LLM er
        alt None, og fragmentet rendres ikke."""
        if not llm.enabled():
            return {prefiks: None, f"{prefiks}_kjorer": False, f"{prefiks}_feil": None}
        if start_hvis_gammel and not self.er_i_gang() and not self.er_ferskt():
            start_hvis_gammel()
        return {
            prefiks: self.last(),
            f"{prefiks}_kjorer": self.er_i_gang(),
            f"{prefiks}_feil": self.siste_feil(),
        }
