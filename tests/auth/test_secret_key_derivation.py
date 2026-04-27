"""I6 regression: Flask SECRET_KEY must derive from master key via HKDF,
never fall back to the placeholder. Single source of truth for keying.
"""
from backend.auth.crypto import derive_key


def test_flask_secret_key_derived_from_master(app):
    master = app.config["SECRET_KEY_BYTES"]
    flask_key = app.config["SECRET_KEY"]
    assert master == bytes.fromhex("0" * 64)  # set by conftest
    assert flask_key == derive_key(master, info=b"flask-session")
    assert len(flask_key) == 32


def test_flask_secret_key_is_not_placeholder(app):
    flask_key = app.config["SECRET_KEY"]
    # The old fallback was the literal string "change-me-in-production".
    # Whatever Flask is signing with now must not be that.
    assert flask_key != "change-me-in-production"
    assert flask_key != b"change-me-in-production"


def test_flask_secret_key_distinct_from_master(app):
    """HKDF domain separation: flask-session subkey must differ from
    the master so leaks of one don't reveal the other."""
    master = app.config["SECRET_KEY_BYTES"]
    flask_key = app.config["SECRET_KEY"]
    assert master != flask_key
