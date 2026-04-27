"""AES-GCM symmetric encryption for storing MFA secrets at rest.

Key is derived from SECRET_KEY via HKDF with a domain-separation `info`
parameter so different uses of the master key produce different subkeys.
"""
import os
import base64

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes


def derive_key(secret_key: bytes, info: bytes) -> bytes:
    if len(secret_key) < 32:
        raise ValueError("SECRET_KEY must be at least 32 bytes")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=info,
    ).derive(secret_key)


def encrypt_secret(plaintext: str, secret_key: bytes, info: bytes) -> str:
    key = derive_key(secret_key, info)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def decrypt_secret(token: str, secret_key: bytes, info: bytes) -> str:
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    nonce, ct = raw[:12], raw[12:]
    key = derive_key(secret_key, info)
    return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")
