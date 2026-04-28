import importlib


def test_default_db_targets_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_CONF", str(tmp_path / "missing.conf"))
    monkeypatch.setenv("CONF_FILE", str(tmp_path / "missing-global.conf"))
    monkeypatch.delenv("DB_BACKUP_TARGETS", raising=False)
    import backend.config
    importlib.reload(backend.config)
    assert backend.config.Config.DB_BACKUP_TARGETS == ""


def test_db_targets_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_CONF", str(tmp_path / "missing.conf"))
    monkeypatch.setenv("CONF_FILE", str(tmp_path / "missing-global.conf"))
    monkeypatch.setenv("DB_BACKUP_TARGETS", "postgres:foo")
    import backend.config
    importlib.reload(backend.config)
    assert backend.config.Config.DB_BACKUP_TARGETS == "postgres:foo"


def test_pg_password_loaded_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_CONF", str(tmp_path / "missing.conf"))
    monkeypatch.setenv("CONF_FILE", str(tmp_path / "missing-global.conf"))
    monkeypatch.setenv("DB_PG_PASSWORD", "supersecret")
    import backend.config
    importlib.reload(backend.config)
    assert backend.config.Config.DB_PG_PASSWORD == "supersecret"


def test_mongo_uri_default_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_CONF", str(tmp_path / "missing.conf"))
    monkeypatch.setenv("CONF_FILE", str(tmp_path / "missing-global.conf"))
    monkeypatch.delenv("DB_MONGO_URI", raising=False)
    import backend.config
    importlib.reload(backend.config)
    assert backend.config.Config.DB_MONGO_URI == ""
