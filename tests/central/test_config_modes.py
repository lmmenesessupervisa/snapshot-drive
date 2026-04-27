"""La clase Config respeta MODE/CENTRAL_* desde env y desde local.conf."""
import importlib
from pathlib import Path


def _reload_config(monkeypatch, env=None, local_conf_text=None, tmp_path=None):
    """Helper: rebuild Config con env y local.conf controlados."""
    if local_conf_text is not None:
        p = tmp_path / "local.conf"
        p.write_text(local_conf_text)
        monkeypatch.setenv("LOCAL_CONF", str(p))
    if env:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    # Forzar reload del módulo para releer _CONF
    import backend.config as cfg
    importlib.reload(cfg)
    return cfg.Config


def test_mode_default_is_client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_CONF", str(tmp_path / "nonexistent.conf"))
    Config = _reload_config(monkeypatch)
    assert Config.MODE == "client"


def test_mode_central_from_local_conf(monkeypatch, tmp_path):
    Config = _reload_config(monkeypatch,
                            local_conf_text='MODE="central"\n',
                            tmp_path=tmp_path)
    assert Config.MODE == "central"


def test_central_url_from_local_conf(monkeypatch, tmp_path):
    txt = 'CENTRAL_URL="https://central.example.com"\nCENTRAL_TOKEN="abc123"\n'
    Config = _reload_config(monkeypatch, local_conf_text=txt, tmp_path=tmp_path)
    assert Config.CENTRAL_URL == "https://central.example.com"
    assert Config.CENTRAL_TOKEN == "abc123"


def test_unknown_mode_falls_back_to_client(monkeypatch, tmp_path):
    Config = _reload_config(monkeypatch,
                            local_conf_text='MODE="nonsense"\n',
                            tmp_path=tmp_path)
    assert Config.MODE == "client"


def test_central_timeout_default_5s(monkeypatch, tmp_path):
    Config = _reload_config(monkeypatch, local_conf_text='', tmp_path=tmp_path)
    assert Config.CENTRAL_TIMEOUT_S == 5
