import importlib


def test_default_thresholds(monkeypatch, tmp_path):
    for k in ("ALERTS_NO_HEARTBEAT_HOURS", "ALERTS_SHRINK_PCT",
              "ALERTS_EMAIL", "ALERTS_WEBHOOK"):
        monkeypatch.delenv(k, raising=False)
    # Point local conf to a non-existent path so it doesn't pull values
    monkeypatch.setenv("LOCAL_CONF", str(tmp_path / "missing.conf"))
    monkeypatch.setenv("CONF_FILE", str(tmp_path / "missing-global.conf"))
    import backend.config
    importlib.reload(backend.config)
    from backend.config import Config
    assert Config.ALERTS_NO_HEARTBEAT_HOURS == 48
    assert Config.ALERTS_SHRINK_PCT == 20
    assert Config.ALERTS_EMAIL == ""
    assert Config.ALERTS_WEBHOOK == ""


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_CONF", str(tmp_path / "missing.conf"))
    monkeypatch.setenv("CONF_FILE", str(tmp_path / "missing-global.conf"))
    monkeypatch.setenv("ALERTS_NO_HEARTBEAT_HOURS", "12")
    monkeypatch.setenv("ALERTS_SHRINK_PCT", "30")
    monkeypatch.setenv("ALERTS_EMAIL", "ops@x.com")
    monkeypatch.setenv("ALERTS_WEBHOOK", "https://hook.example/x")
    import backend.config
    importlib.reload(backend.config)
    from backend.config import Config
    assert Config.ALERTS_NO_HEARTBEAT_HOURS == 12
    assert Config.ALERTS_SHRINK_PCT == 30
    assert Config.ALERTS_EMAIL == "ops@x.com"
    assert Config.ALERTS_WEBHOOK == "https://hook.example/x"
