import pytest
from backend.auth.passwords import hash_password, verify_password


def test_hash_verify_round_trip():
    h = hash_password("CorrectHorseBattery!2026")
    assert verify_password("CorrectHorseBattery!2026", h) is True


def test_verify_wrong_password():
    h = hash_password("a-good-password-12345")
    assert verify_password("not-the-password", h) is False


def test_hash_different_each_call():
    a = hash_password("same")
    b = hash_password("same")
    assert a != b  # salt makes them differ


def test_hash_uses_argon2id():
    h = hash_password("x")
    assert h.startswith("$argon2id$")
