"""Symmetric encryption for secrets stored at rest (currently: BioTime server
passwords on BiometricDevice). Reuses the app's existing SECRET_KEY rather
than introducing a second key to provision/rotate — the key already signs
every JWT in the app, so this doesn't lower the bar for what compromising it
would mean.
"""
import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


@lru_cache
def _fernet() -> Fernet:
    key = hashlib.sha256(get_settings().secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Could not decrypt stored secret — SECRET_KEY may have changed") from exc
