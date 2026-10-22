"""Encryption of secrets stored in the database (e.g. mail passwords).

Fernet (AES-128-CBC + HMAC) with the key from JARVIS_ENCRYPTION_KEY, so a copy of the database alone
does not reveal credentials. Several comma-separated keys allow rotation: the first encrypts, all decrypt.
"""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from .config import settings


class CryptoError(Exception):
    pass


def _fernet() -> MultiFernet:
    keys = [k.strip() for k in settings.encryption_key.split(",") if k.strip()]
    if not keys:
        raise CryptoError("JARVIS_ENCRYPTION_KEY is not set (run `make encryption-key`)")
    try:
        return MultiFernet([Fernet(k) for k in keys])
    except (ValueError, TypeError) as exc:
        raise CryptoError("JARVIS_ENCRYPTION_KEY is not a valid Fernet key") from exc


def available() -> bool:
    try:
        _fernet()
        return True
    except CryptoError:
        return False


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise CryptoError("cannot decrypt: wrong or missing key") from exc
