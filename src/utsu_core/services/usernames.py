"""Atomic reservations for optional, changeable public usernames."""
from __future__ import annotations

import secrets

from fastapi import HTTPException

from .. import security
from ..store import Store


def username_key(secret: str, username: str) -> str:
    return security.username_uuid(secret, username)


def reserve(store: Store, key: str, uid: str) -> str:
    token = secrets.token_hex(16)

    def claim(current):
        if current and current.get("uuid"):
            return None
        return {"uuid": uid, "token": token}

    result = store.update("usernames", key, claim)
    if not result or result.get("token") != token:
        raise HTTPException(409, "That username is already in use")
    return token


def release(store: Store, key: str, token: str) -> None:
    def clear(current):
        if current and current.get("token") == token:
            return {"uuid": None, "token": None}
        return None

    store.update("usernames", key, clear)
