import pytest
from backend.auth.crypto import encrypt_secret, decrypt_secret, derive_key


SECRET_KEY = bytes.fromhex("0" * 64)


def test_round_trip():
    plaintext = "JBSWY3DPEHPK3PXP"
    ct = encrypt_secret(plaintext, SECRET_KEY, info=b"mfa")
    assert decrypt_secret(ct, SECRET_KEY, info=b"mfa") == plaintext


def test_ciphertext_changes_each_call():
    ct1 = encrypt_secret("x", SECRET_KEY, info=b"mfa")
    ct2 = encrypt_secret("x", SECRET_KEY, info=b"mfa")
    assert ct1 != ct2  # nonce makes it different


def test_wrong_info_fails():
    ct = encrypt_secret("x", SECRET_KEY, info=b"mfa")
    with pytest.raises(Exception):
        decrypt_secret(ct, SECRET_KEY, info=b"other")


def test_wrong_key_fails():
    ct = encrypt_secret("x", SECRET_KEY, info=b"mfa")
    with pytest.raises(Exception):
        decrypt_secret(ct, bytes.fromhex("1" * 64), info=b"mfa")


def test_derive_key_deterministic():
    k1 = derive_key(SECRET_KEY, info=b"mfa")
    k2 = derive_key(SECRET_KEY, info=b"mfa")
    assert k1 == k2 and len(k1) == 32
