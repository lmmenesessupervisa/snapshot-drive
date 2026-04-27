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


# ---------------------------------------------------------------------------
# Task 6: Password policy validation
# ---------------------------------------------------------------------------
import zxcvbn as _zxcvbn


class PolicyError(ValueError):
    pass


MIN_LEN = 12
MIN_ZXCVBN_SCORE = 3


def validate_policy(password: str, *, email: str, display_name: str) -> None:
    if len(password) < MIN_LEN:
        raise PolicyError(f"password must be at least {MIN_LEN} characters")
    lp = password.lower()
    local_part = email.lower().split("@")[0] if email else ""
    if len(local_part) > 1 and local_part in lp:
        raise PolicyError("password must not contain your email")
    if display_name and len(display_name) > 1 and display_name.lower() in lp:
        raise PolicyError("password must not contain your name")
    score = _zxcvbn.zxcvbn(password)["score"]
    if score < MIN_ZXCVBN_SCORE:
        raise PolicyError(
            f"password too weak (strength {score}/4, need {MIN_ZXCVBN_SCORE})"
        )
