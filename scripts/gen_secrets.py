"""Print fresh secrets for .env: session key, setup token and a VAPID key pair."""

import base64
import secrets

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


key = ec.generate_private_key(ec.SECP256R1())
public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
private = key.private_numbers().private_value.to_bytes(32, "big")

print(f"JARVIS_SECRET_KEY={secrets.token_urlsafe(48)}")
print(f"JARVIS_SETUP_TOKEN={secrets.token_urlsafe(24)}")
print(f"JARVIS_VAPID_PUBLIC_KEY={b64url(public)}")
print(f"JARVIS_VAPID_PRIVATE_KEY={b64url(private)}")
