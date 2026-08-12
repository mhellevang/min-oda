"""Single-flight-jobbkjører for LLM-genereringer som tar minutter. Delt av
forslag.py (handleliste) og innsikt_llm.py (innsikt): én daemon-tråd om
gangen per jobb, siste feil eksponert for UI-et."""

from __future__ import annotations

import threading
from typing import Callable


class Jobb:
    def __init__(self, navn: str):
        self._navn = navn
        self._lock = threading.Lock()
        self._kjorer = False
        self._siste_feil: str | None = None

    def er_i_gang(self) -> bool:
        return self._kjorer

    def siste_feil(self) -> str | None:
        """Feilmelding fra forrige kjøring, None hvis den lyktes."""
        return self._siste_feil

    def start(self, fn: Callable[[], dict]) -> threading.Thread | None:
        """Kjør fn() i en daemon-tråd. En 'feil'-nøkkel i returverdien (eller
        et unntak) registreres som siste feil. Returnerer tråden, eller None
        hvis en jobb allerede kjører (single-flight)."""
        with self._lock:
            if self._kjorer:
                return None
            self._kjorer = True

        def _run() -> None:
            try:
                resultat = fn()
                self._siste_feil = (
                    resultat.get("feil") if isinstance(resultat, dict) else None
                )
            except Exception as e:  # en trådkrasj må aldri låse jobben
                self._siste_feil = f"Genereringen feilet: {e}"
            finally:
                self._kjorer = False

        t = threading.Thread(target=_run, daemon=True, name=self._navn)
        t.start()
        return t
