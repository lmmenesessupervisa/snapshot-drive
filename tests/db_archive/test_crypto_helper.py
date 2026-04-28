import os
import shutil
import subprocess
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HELPER = os.path.join(REPO, "core/lib/crypto.sh")


def _run(snippet, env=None, stdin=None):
    """Source crypto.sh and run the snippet. Returns (stdout, stderr, rc)."""
    full = (
        f'export SNAPSHOT_ROOT="{REPO}"\n'
        f'set -Eeuo pipefail\n'
        f'_log_stub() {{ :; }}\n'
        f'log_info()  {{ _log_stub; }}\n'
        f'log_warn()  {{ _log_stub; }}\n'
        f'log_error() {{ _log_stub; }}\n'
        f'die()       {{ echo "die: $*" >&2; exit 1; }}\n'
        f'source "{HELPER}"\n'
        f'{snippet}\n'
    )
    p = subprocess.run(["bash", "-c", full],
                       input=stdin, capture_output=True,
                       env={**os.environ, **(env or {})})
    return p.stdout, p.stderr, p.returncode


def test_crypto_mode_none_when_nothing_set():
    out, _, rc = _run('crypto_mode',
                      env={"ARCHIVE_AGE_RECIPIENTS": "", "ARCHIVE_PASSWORD": ""})
    assert rc == 0
    assert out.strip() == b"none"


def test_crypto_mode_openssl_when_password():
    out, _, rc = _run('crypto_mode',
                      env={"ARCHIVE_AGE_RECIPIENTS": "", "ARCHIVE_PASSWORD": "x"})
    assert out.strip() == b"openssl"


def test_crypto_mode_age_when_recipients():
    out, _, rc = _run('crypto_mode',
                      env={"ARCHIVE_AGE_RECIPIENTS": "age1abc", "ARCHIVE_PASSWORD": ""})
    assert out.strip() == b"age"


def test_crypto_mode_age_wins_over_password():
    out, _, rc = _run('crypto_mode',
                      env={"ARCHIVE_AGE_RECIPIENTS": "age1abc", "ARCHIVE_PASSWORD": "x"})
    assert out.strip() == b"age"


def test_crypto_extension_matches_mode():
    out, _, _ = _run('crypto_extension',
                     env={"ARCHIVE_AGE_RECIPIENTS": "age1abc"})
    assert out.strip() == b"age"
    out, _, _ = _run('crypto_extension',
                     env={"ARCHIVE_AGE_RECIPIENTS": "", "ARCHIVE_PASSWORD": "x"})
    assert out.strip() == b"enc"
    out, _, _ = _run('crypto_extension',
                     env={"ARCHIVE_AGE_RECIPIENTS": "", "ARCHIVE_PASSWORD": ""})
    assert out.strip() == b""


def test_openssl_round_trip(tmp_path):
    plaintext = b"hello world\n" * 100
    enc = tmp_path / "enc.bin"
    out, err, rc = _run(
        f'crypto_encrypt_pipe > "{enc}"',
        env={"ARCHIVE_AGE_RECIPIENTS": "", "ARCHIVE_PASSWORD": "topsecret"},
        stdin=plaintext,
    )
    assert rc == 0, err
    assert enc.exists() and enc.stat().st_size > 0
    dec_out, err, rc = _run(
        f'crypto_decrypt_for_path "x.zst.enc" < "{enc}"',
        env={"ARCHIVE_AGE_RECIPIENTS": "", "ARCHIVE_PASSWORD": "topsecret"},
    )
    assert rc == 0, err
    assert dec_out == plaintext


@pytest.mark.skipif(
    shutil.which("age") is None
    and not os.path.exists("/opt/snapshot-V3/bundle/bin/age"),
    reason="age binary not available",
)
def test_age_round_trip(tmp_path):
    age_keygen = (
        shutil.which("age-keygen") or "/opt/snapshot-V3/bundle/bin/age-keygen"
    )
    p = subprocess.run([age_keygen], capture_output=True, text=True)
    assert p.returncode == 0
    pub = ""
    for line in p.stdout.splitlines():
        if line.startswith("# public key:"):
            pub = line.split(":", 1)[1].strip()
            break
    assert pub.startswith("age1")
    identity_file = tmp_path / "identity.txt"
    identity_file.write_text(p.stdout)
    plaintext = b"super secret data\n" * 50
    enc = tmp_path / "enc.age"
    _, err, rc = _run(
        f'crypto_encrypt_pipe > "{enc}"',
        env={"ARCHIVE_AGE_RECIPIENTS": pub, "ARCHIVE_PASSWORD": ""},
        stdin=plaintext,
    )
    assert rc == 0, err
    dec_out, err, rc = _run(
        f'crypto_decrypt_for_path "x.zst.age" < "{enc}"',
        env={"ARCHIVE_AGE_RECIPIENTS": pub,
             "ARCHIVE_AGE_IDENTITY_FILE": str(identity_file),
             "ARCHIVE_PASSWORD": ""},
    )
    assert rc == 0, err
    assert dec_out == plaintext


def test_passthrough_round_trip(tmp_path):
    plaintext = b"no crypto here\n" * 30
    enc = tmp_path / "enc.bin"
    _, err, rc = _run(
        f'crypto_encrypt_pipe > "{enc}"',
        env={"ARCHIVE_AGE_RECIPIENTS": "", "ARCHIVE_PASSWORD": ""},
        stdin=plaintext,
    )
    assert rc == 0, err
    assert enc.read_bytes() == plaintext
    dec_out, _, rc = _run(
        f'crypto_decrypt_for_path "x.zst" < "{enc}"',
        env={"ARCHIVE_AGE_RECIPIENTS": "", "ARCHIVE_PASSWORD": ""},
    )
    assert rc == 0
    assert dec_out == plaintext
