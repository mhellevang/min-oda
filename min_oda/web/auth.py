"""Enkelt app-passord foran hele min-oda. Ett delt passord (APP_PASSWORD)
for de få som skal ha tilgang. Er passordet tomt, er auth av og alt er
åpent (greit lokalt eller bak VPN).

Cookien er et HMAC-token utledet fra SESSION_SECRET (faller tilbake på
passordet), så den kan ikke forfalskes uten å kjenne hemmeligheten.
Stateless, ingen ekstra avhengigheter. Samme mønster (og begrensninger)
som avisa: tokenet er konstant per hemmelighet (ingen per-login-nonce,
intet utløp i selve tokenet, kun cookiens max_age), så tilbakekalling
krever å rotere SESSION_SECRET/APP_PASSWORD, og den som får tak i cookien
beholder tilgang til da. Akseptabel avveining for en app med et par
brukere bak TLS. Sett COOKIE_SECURE=true når den serveres over HTTPS."""

from __future__ import annotations

import hashlib
import hmac
import os

from dotenv import load_dotenv

load_dotenv()

COOKIE_NAME = "min_oda_session"


def _password() -> str:
    return os.environ.get("APP_PASSWORD", "").strip()


def auth_enabled() -> bool:
    return bool(_password())


def cookie_secure() -> bool:
    return os.environ.get("COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes")


def _secret() -> bytes:
    base = os.environ.get("SESSION_SECRET", "").strip() or _password()
    return base.encode("utf-8")


def make_token() -> str:
    return hmac.new(_secret(), b"min-oda-app", hashlib.sha256).hexdigest()


def check_password(pw: str) -> bool:
    return hmac.compare_digest(pw or "", _password())


def is_authed(request) -> bool:
    if not auth_enabled():
        return True
    token = request.cookies.get(COOKIE_NAME, "")
    return hmac.compare_digest(token, make_token())
