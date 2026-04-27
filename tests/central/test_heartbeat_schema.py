import pytest
from backend.central.schema import validate_heartbeat, SchemaError


def _good_payload() -> dict:
    return {
        "event_id": "9f2a1b6c-1234-5678-9abc-def012345678",
        "ts": "2026-04-27T17:42:11Z",
        "client": {"proyecto": "superaccess-uno", "entorno": "cloud", "pais": "colombia"},
        "target": {"category": "os", "subkey": "linux", "label": "web01"},
        "operation": {"op": "archive", "status": "ok",
                      "started_at": "2026-04-27T17:30:02Z",
                      "duration_s": 729, "error": None},
        "snapshot": {
            "size_bytes": 4831838208,
            "remote_path": "superaccess-uno/cloud/colombia/os/linux/web01/2026/04/27/x.tar.zst.enc",
            "encrypted": True,
        },
        "totals": {"size_bytes": 178291823104, "count_files": 14,
                   "oldest_ts": "2025-05-01T02:14:33Z",
                   "newest_ts": "2026-04-27T17:42:11Z"},
        "host_meta": {"hostname": "web01.local",
                      "snapctl_version": "0.4.2",
                      "rclone_version": "v1.68.2"},
    }


def test_good_payload_passes():
    out = validate_heartbeat(_good_payload())
    assert out["client"]["proyecto"] == "superaccess-uno"


def test_missing_event_id_fails():
    p = _good_payload(); del p["event_id"]
    with pytest.raises(SchemaError, match="event_id"):
        validate_heartbeat(p)


def test_event_id_must_be_uuid_format():
    p = _good_payload(); p["event_id"] = "not-a-uuid"
    with pytest.raises(SchemaError, match="event_id.*uuid"):
        validate_heartbeat(p)


def test_invalid_category_fails():
    p = _good_payload(); p["target"]["category"] = "windows"
    with pytest.raises(SchemaError, match="category"):
        validate_heartbeat(p)


def test_invalid_status_fails():
    p = _good_payload(); p["operation"]["status"] = "weird"
    with pytest.raises(SchemaError, match="status"):
        validate_heartbeat(p)


def test_negative_size_fails():
    p = _good_payload(); p["snapshot"]["size_bytes"] = -1
    with pytest.raises(SchemaError, match="size_bytes"):
        validate_heartbeat(p)


def test_proyecto_must_match_path_regex():
    """Sin caracteres raros que rompan el path en Drive."""
    p = _good_payload(); p["client"]["proyecto"] = "../etc/passwd"
    with pytest.raises(SchemaError, match="proyecto"):
        validate_heartbeat(p)


def test_optional_host_meta_can_be_omitted():
    p = _good_payload(); del p["host_meta"]
    out = validate_heartbeat(p)
    assert out.get("host_meta") in (None, {})


def test_oversize_payload_via_check_helper():
    from backend.central.schema import check_size
    big = "x" * 70_000
    with pytest.raises(SchemaError, match="too large"):
        check_size(big.encode())
