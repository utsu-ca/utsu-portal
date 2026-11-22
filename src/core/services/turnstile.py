"""Server-side verification of Cloudflare Turnstile registration challenges."""
from __future__ import annotations

import json
import logging
from urllib.error import URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from fastapi import HTTPException

from ..settings import Settings

log = logging.getLogger(__name__)
SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def configuration(settings: Settings) -> dict:
    if settings.turnstile_site_key and settings.turnstile_secret_key:
        return {"site_key": settings.turnstile_site_key, "local_bypass": False}
    if settings.dev_admin and not settings.turnstile_site_key and not settings.turnstile_secret_key:
        return {"site_key": None, "local_bypass": True}
    raise HTTPException(503, "Registration challenge is not configured. Contact the portal administrator.")


def verify_registration(settings: Settings, token: str) -> None:
    config = configuration(settings)
    if config["local_bypass"]:
        return
    if not token or len(token) > 2048:
        raise HTTPException(422, "Complete the registration challenge and try again.")
    data = urlencode({"secret": settings.turnstile_secret_key, "response": token}).encode()
    request = Request(SITEVERIFY, data=data, method="POST")
    try:
        with urlopen(request, timeout=5) as response:
            result = json.load(response)
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        log.warning("Registration challenge verification unavailable: %s", type(exc).__name__)
        raise HTTPException(503, "Registration challenge is unavailable. Try again later.") from exc
    if (not isinstance(result, dict) or result.get("success") is not True
            or result.get("action") != "register"
            or result.get("hostname") != urlsplit(settings.sso_issuer).hostname):
        raise HTTPException(422, "Registration challenge failed or expired. Complete it again.")
