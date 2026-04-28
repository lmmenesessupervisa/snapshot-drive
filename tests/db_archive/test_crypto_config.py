import importlib


def test_default_age_recipients_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_CONF", str(tmp_path / "missing.conf"))
    monkeypatch.setenv("CONF_FILE", str(tmp_path / "missing-global.conf"))
    monkeypatch.delenv("ARCHIVE_AGE_RECIPIENTS", raising=False)
    monkeypatch.delenv("ARCHIVE_AGE_IDENTITY_FILE", raising=False)
    import backend.config
    importlib.reload(backend.config)
    assert backend.config.Config.ARCHIVE_AGE_RECIPIENTS == ""
    assert backend.config.Config.ARCHIVE_AGE_IDENTITY_FILE == ""


def test_age_recipients_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_CONF", str(tmp_path / "missing.conf"))
    monkeypatch.setenv("CONF_FILE", str(tmp_path / "missing-global.conf"))
    monkeypatch.setenv("ARCHIVE_AGE_RECIPIENTS", "age1xyz age1abc")
    import backend.config
    importlib.reload(backend.config)
    assert backend.config.Config.ARCHIVE_AGE_RECIPIENTS == "age1xyz age1abc"


def test_age_identity_file_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_CONF", str(tmp_path / "missing.conf"))
    monkeypatch.setenv("CONF_FILE", str(tmp_path / "missing-global.conf"))
    monkeypatch.setenv("ARCHIVE_AGE_IDENTITY_FILE", "/var/lib/snapshot-v3/age-id.txt")
    import backend.config
    importlib.reload(backend.config)
    assert backend.config.Config.ARCHIVE_AGE_IDENTITY_FILE == "/var/lib/snapshot-v3/age-id.txt"
