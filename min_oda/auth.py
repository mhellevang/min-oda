"""Hent oda.com-cookies direkte fra nettleserens cookie-store, så
sluttbrukeren slipper å kopiere sessionid manuelt fra DevTools.

Bygger på rookiepy som leser cookie-databasen til Firefox/Chrome/Safari osv.
Prøver en plattform-spesifikk rekkefølge til den finner en nettleser hvor
brukeren faktisk er logget inn på oda.com.
"""

from __future__ import annotations

import logging
import sys

import rookiepy

log = logging.getLogger("min-oda.auth")

# Firefox først fordi den ikke trigger macOS Keychain-prompt. Deretter
# Chromium-baserte nettlesere, så Safari på Mac.
_ORDER_MAC = ["firefox", "chrome", "brave", "edge", "arc", "opera", "vivaldi", "safari"]
_ORDER_WINDOWS = ["firefox", "chrome", "edge", "brave", "opera", "vivaldi"]
_ORDER_LINUX = ["firefox", "librewolf", "chrome", "chromium", "brave", "opera", "vivaldi"]


def _platform_order() -> list[str]:
    if sys.platform == "darwin":
        return _ORDER_MAC
    if sys.platform.startswith("win"):
        return _ORDER_WINDOWS
    return _ORDER_LINUX


def _extract(browser: str) -> dict[str, str] | None:
    """Returnerer cookies for oda.com fra én navngitt nettleser, eller None
    hvis nettleseren ikke er installert, ikke har sessionid for oda.com,
    eller cookie-fila ikke kan leses."""
    fn = getattr(rookiepy, browser, None)
    if fn is None:
        return None
    try:
        cookies = fn(["oda.com"])
    except Exception as e:
        log.debug("Kunne ikke lese cookies fra %s: %s", browser, e)
        return None

    found: dict[str, str] = {}
    for c in cookies:
        name = c.get("name")
        if name in ("sessionid", "csrftoken"):
            found[name] = c.get("value", "")
    if found.get("sessionid"):
        return found
    return None


def load_browser_cookies(
    browser: str | None = None,
) -> tuple[dict[str, str], str] | None:
    """Returner ({sessionid, csrftoken?}, browser_name) når vi finner Oda-cookies,
    ellers None.

    `browser` overstyrer plattformens default-rekkefølge — typisk satt via
    miljøvariabelen ODA_BROWSER ('firefox', 'chrome', 'safari', ...).
    """
    candidates = [browser] if browser else _platform_order()
    for name in candidates:
        result = _extract(name)
        if result:
            return result, name
    return None
