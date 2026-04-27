"""Password hashing and policy validation."""
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError


_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=4,
    hash_len=32,
)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hash_: str) -> bool:
    try:
        _hasher.verify(hash_, password)
        return True
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def needs_rehash(hash_: str) -> bool:
    return _hasher.check_needs_rehash(hash_)
