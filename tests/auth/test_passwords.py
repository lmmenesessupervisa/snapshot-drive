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


# ---------------------------------------------------------------------------
# Task 6: Password policy validation
# ---------------------------------------------------------------------------
from backend.auth.passwords import validate_policy, PolicyError


def test_too_short():
    with pytest.raises(PolicyError) as e:
        validate_policy("short", email="a@b.c", display_name="A")
    assert "12" in str(e.value)


def test_low_zxcvbn_score():
    with pytest.raises(PolicyError):
        validate_policy("password1234", email="a@b.c", display_name="A")


def test_contains_email():
    with pytest.raises(PolicyError):
        validate_policy("juan@empresa.com-xxxx-Strong!", email="juan@empresa.com", display_name="Juan")


def test_contains_display_name():
    with pytest.raises(PolicyError):
        validate_policy("Juan-very-strong-passw0rd!", email="x@y.z", display_name="Juan")


def test_strong_passes():
    validate_policy("Tr0ub4dor&3-mighty-stallion", email="a@b.c", display_name="A")
