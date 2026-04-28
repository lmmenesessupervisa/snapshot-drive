from backend.central.alerts.rules import evaluate_heartbeat, resolves_keys


def _payload(missing=None, total_bytes=1_000_000_000):
    return {
        "host_meta": {"hostname": "h", "missing_paths": missing or []},
        "totals": {"size_bytes": total_bytes},
    }


def test_no_findings_when_clean():
    out = evaluate_heartbeat(
        _payload(missing=[], total_bytes=1_000_000_000),
        prev_size_bytes=1_000_000_000,
        thresholds={"shrink_pct": 20},
    )
    assert out == []


def test_folder_missing_fired():
    out = evaluate_heartbeat(
        _payload(missing=["/etc/x"], total_bytes=1000),
        prev_size_bytes=1000,
        thresholds={"shrink_pct": 20},
    )
    folder = next(f for f in out if f["type"] == "folder_missing")
    assert folder["severity"] == "warning"
    assert folder["detail"]["missing_paths"] == ["/etc/x"]


def test_backup_shrink_fired_critical_when_50pct():
    out = evaluate_heartbeat(
        _payload(missing=[], total_bytes=500),
        prev_size_bytes=1000,
        thresholds={"shrink_pct": 20},
    )
    shrink = next(f for f in out if f["type"] == "backup_shrink")
    assert shrink["severity"] == "critical"
    assert shrink["detail"]["pct"] == 50.0
    assert shrink["detail"]["prev_bytes"] == 1000
    assert shrink["detail"]["new_bytes"] == 500


def test_backup_shrink_warning_below_50pct():
    out = evaluate_heartbeat(
        _payload(missing=[], total_bytes=750),
        prev_size_bytes=1000,
        thresholds={"shrink_pct": 20},
    )
    shrink = next(f for f in out if f["type"] == "backup_shrink")
    assert shrink["severity"] == "warning"


def test_backup_shrink_skipped_when_under_threshold():
    out = evaluate_heartbeat(
        _payload(missing=[], total_bytes=900),
        prev_size_bytes=1000,
        thresholds={"shrink_pct": 20},
    )
    assert not any(f["type"] == "backup_shrink" for f in out)


def test_backup_shrink_skipped_when_no_prev():
    out = evaluate_heartbeat(
        _payload(total_bytes=1),
        prev_size_bytes=None,
        thresholds={"shrink_pct": 20},
    )
    assert not any(f["type"] == "backup_shrink" for f in out)


def test_resolves_keys_clean_resolves_folder_missing():
    keys = resolves_keys(_payload(missing=[]))
    assert "folder_missing" in keys
    assert "no_heartbeat" in keys
    assert "backup_shrink" not in keys


def test_resolves_keys_with_missing_only_resolves_no_heartbeat():
    keys = resolves_keys(_payload(missing=["/x"]))
    assert "no_heartbeat" in keys
    assert "folder_missing" not in keys
