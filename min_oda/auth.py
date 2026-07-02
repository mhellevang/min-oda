"""Skaff oda.com-credentials på tre måter, i fallende prioritet (rekkefølgen
velges i oda_client.build_client):

1. Passord-login mot Oda (login_with_password) — for headless drift på
   NAS/server der det ikke finnes en innlogget nettleser. Sessionen buffres
   til data/session.json så vi ikke logger inn på nytt for hvert kall.
2. Cookies fra nettleserens cookie-store via rookiepy (load_browser_cookies)
   — for lokal bruk på egen maskin.
3. Manuell cookie i .env (håndteres i oda_client).

rookiepy leser cookie-databasen til Firefox/Chrome/Safari osv. og prøver en
plattform-spesifikk rekkefølge til den finner en nettleser hvor brukeren
faktisk er logget inn på oda.com.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import httpx

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
    try:
        import rookiepy
    except ImportError:
        # rookiepy er valgfri i headless-drift (passord-login brukes i stedet).
        log.debug("rookiepy ikke tilgjengelig — hopper over nettleser-cookies")
        return None
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


# --- Passord-login mot Oda -------------------------------------------------
#
# Oda har et udokumentert Django-login-endepunkt: hent csrftoken fra login-
# siden, POST username/password med X-CSRFToken-header, og få en autentisert
# sessionid tilbake. Bekreftet ved test juli 2026. Ingen captcha per nå, men
# Oda kan innføre captcha/2FA når som helst, så dette er bevisst skjørt og
# feiler tydelig (LoginFailed) slik at man kan falle tilbake på manuell cookie.

LOGIN_PAGE_URL = "https://oda.com/no/user/login/"
LOGIN_API_URL = "https://oda.com/api/v1/user/login/"

_SESSION_CACHE = Path(__file__).parent.parent / "data" / "session.json"
# sessionid lever 30 dager hos Oda. Vi re-logger inn i god tid før det for
# å unngå at en utløpt buffer trigger en mislykket henting.
_SESSION_MAX_AGE_S = 25 * 24 * 3600


class LoginFailed(RuntimeError):
    """Reises når passord-login mot Oda ikke gir en autentisert sesjon
    (feil brukernavn/passord, uventet svar, captcha/2FA, e.l.)."""


def _login_error_message(r: httpx.Response) -> str:
    """Plukker ut en lesbar feilmelding fra Odas login-svar. Oda svarer med
    {"errors": {"__all__": ["Feil brukernavn eller passord ..."]}} ved feil
    credentials, og {"errors": {"felt": {...}}} ved valideringsfeil."""
    try:
        errs = r.json().get("errors")
    except Exception:
        return f"Oda-login feilet (HTTP {r.status_code})."
    if isinstance(errs, dict):
        allmsgs = errs.get("__all__")
        if isinstance(allmsgs, list) and allmsgs:
            return "; ".join(str(m) for m in allmsgs)
        parts = [f"{k}: {v}" for k, v in errs.items()]
        if parts:
            return "; ".join(parts)
    if isinstance(errs, list) and errs:
        return "; ".join(str(m) for m in errs)
    return f"Oda-login feilet (HTTP {r.status_code})."


def login_with_password(
    username: str, password: str, user_agent: str
) -> dict[str, str]:
    """Logg inn mot Oda og returner {'sessionid', 'csrftoken'}.

    Reiser LoginFailed hvis innloggingen ikke går gjennom. Suksess avgjøres
    på HTTP-status (200), ikke på om en sessionid-cookie finnes — Oda setter
    en anonym sessionid også ved feil.
    """
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.8",
    }
    with httpx.Client(
        headers=headers, timeout=30.0, follow_redirects=True
    ) as client:
        try:
            client.get(LOGIN_PAGE_URL)
        except httpx.HTTPError as e:
            raise LoginFailed(f"Fikk ikke kontakt med Oda-login-siden: {e}") from e

        csrf = client.cookies.get("csrftoken")
        if not csrf:
            raise LoginFailed("Fant ingen csrftoken på Oda-login-siden.")

        try:
            r = client.post(
                LOGIN_API_URL,
                json={"username": username, "password": password},
                headers={"X-CSRFToken": csrf, "Referer": LOGIN_PAGE_URL},
            )
        except httpx.HTTPError as e:
            raise LoginFailed(f"Login-forespørselen mot Oda feilet: {e}") from e

        if r.status_code != 200:
            raise LoginFailed(_login_error_message(r))

        # Django roterer sesjonen ved innlogging, så sessionid etter POST er
        # den autentiserte. csrftoken kan også ha rotert — ta begge fra jaren.
        sessionid = client.cookies.get("sessionid")
        csrftoken = client.cookies.get("csrftoken") or ""
        if not sessionid:
            raise LoginFailed("Login ga HTTP 200, men ingen sessionid-cookie.")
        return {"sessionid": sessionid, "csrftoken": csrftoken}


def load_cached_session(
    max_age_s: float = _SESSION_MAX_AGE_S,
) -> dict[str, str] | None:
    """Les bufret sesjon fra data/session.json hvis den finnes og er fersk nok.
    Returnerer {'sessionid', 'csrftoken'} eller None."""
    if not _SESSION_CACHE.exists():
        return None
    try:
        data = json.loads(_SESSION_CACHE.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not data.get("sessionid"):
        return None
    if (time.time() - float(data.get("ts", 0))) > max_age_s:
        return None
    return {"sessionid": data["sessionid"], "csrftoken": data.get("csrftoken", "")}


def save_cached_session(session: dict[str, str]) -> None:
    """Skriv sesjon + tidsstempel til data/session.json."""
    _SESSION_CACHE.parent.mkdir(parents=True, exist_ok=True)
    payload = {**session, "ts": time.time()}
    _SESSION_CACHE.write_text(json.dumps(payload))


def clear_cached_session() -> None:
    """Slett bufret sesjon. Kalles når en henting feiler slik at neste
    forsøk tvinger ny innlogging."""
    _SESSION_CACHE.unlink(missing_ok=True)


def get_session(
    username: str, password: str, user_agent: str, force: bool = False
) -> tuple[dict[str, str], bool]:
    """Skaff en gyldig sesjon: bruk bufret hvis mulig, ellers logg inn på nytt.
    Returnerer (session, from_cache). Reiser LoginFailed hvis innlogging feiler."""
    if not force:
        cached = load_cached_session()
        if cached:
            return cached, True
    session = login_with_password(username, password, user_agent)
    save_cached_session(session)
    return session, False
