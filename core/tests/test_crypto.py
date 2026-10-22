import pytest
from cryptography.fernet import Fernet

from app import crypto
from app.config import settings


def key():
    return Fernet.generate_key().decode()


def test_roundtrip_and_ciphertext_differs(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", key())
    token = crypto.encrypt("hasło-aplikacji")
    assert "hasło" not in token and crypto.decrypt(token) == "hasło-aplikacji"
    assert crypto.encrypt("x") != crypto.encrypt("x")  # random IV


def test_missing_or_invalid_key(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", "")
    assert crypto.available() is False
    with pytest.raises(crypto.CryptoError, match="not set"):
        crypto.encrypt("x")
    monkeypatch.setattr(settings, "encryption_key", "not-a-fernet-key")
    with pytest.raises(crypto.CryptoError, match="valid"):
        crypto.encrypt("x")


def test_wrong_key_cannot_decrypt(monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", key())
    token = crypto.encrypt("secret")
    monkeypatch.setattr(settings, "encryption_key", key())
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(token)


def test_key_rotation(monkeypatch):
    old, new = key(), key()
    monkeypatch.setattr(settings, "encryption_key", old)
    legacy = crypto.encrypt("secret")
    monkeypatch.setattr(settings, "encryption_key", f"{new},{old}")  # new first: encrypts; old still decrypts
    assert crypto.decrypt(legacy) == "secret"
    fresh = crypto.encrypt("secret")
    monkeypatch.setattr(settings, "encryption_key", new)
    assert crypto.decrypt(fresh) == "secret"  # written with the new key
