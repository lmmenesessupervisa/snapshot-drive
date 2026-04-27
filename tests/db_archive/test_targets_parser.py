import pytest
from backend.services.db_targets import parse_targets, ParseError, VALID_ENGINES


def test_empty_string_returns_empty_list():
    assert parse_targets("") == []
    assert parse_targets("   ") == []


def test_single_target():
    assert parse_targets("postgres:mydb") == [("postgres", "mydb")]


def test_multiple_space_separated():
    out = parse_targets("postgres:a mysql:b mongo:c")
    assert out == [("postgres", "a"), ("mysql", "b"), ("mongo", "c")]


def test_extra_whitespace_tolerated():
    assert parse_targets("  postgres:a   mysql:b  ") == [
        ("postgres", "a"), ("mysql", "b")
    ]


def test_engine_validation_rejects_unknown():
    with pytest.raises(ParseError) as e:
        parse_targets("redis:mydb")
    assert "redis" in str(e.value)


def test_missing_colon_raises():
    with pytest.raises(ParseError):
        parse_targets("postgres mydb")


def test_empty_dbname_raises():
    with pytest.raises(ParseError):
        parse_targets("postgres:")


def test_dbname_with_dash_underscore_dot_ok():
    assert parse_targets("postgres:my-db_v2.0") == [("postgres", "my-db_v2.0")]


def test_dbname_with_invalid_chars_rejected():
    with pytest.raises(ParseError):
        parse_targets("postgres:my db")  # space in name
    with pytest.raises(ParseError):
        parse_targets("postgres:'; DROP TABLE--")


def test_valid_engines_constant():
    assert set(VALID_ENGINES) == {"postgres", "mysql", "mongo"}
