"""Unit tests for scripts/diagnose_db.py."""

from __future__ import annotations

from typing import Any

import pytest
from scripts.diagnose_db import (
    clean_input_url,
    compare_targets,
    inspect_role,
    main,
    mask_url,
    parse_and_validate_url,
    resolve_urls,
)

# =============================================================================
# 1. clean_input_url tests
# =============================================================================


def test_clean_input_url_strips_whitespace_and_quotes() -> None:
    assert clean_input_url(None) == ""
    assert clean_input_url("") == ""
    assert clean_input_url("   ") == ""
    assert clean_input_url('""') == ""
    assert clean_input_url("''") == ""
    assert clean_input_url('  "postgresql://host/db"  ') == "postgresql://host/db"
    assert clean_input_url("  'postgresql://host/db'  ") == "postgresql://host/db"
    assert clean_input_url("'\"postgresql://host/db\"'") == "postgresql://host/db"
    assert clean_input_url("postgresql://host/db") == "postgresql://host/db"


# =============================================================================
# 2. mask_url tests
# =============================================================================


def test_mask_url_masks_plain_and_percent_encoded_passwords() -> None:
    assert mask_url(None) == "<not set>"
    assert mask_url("") == "<not set>"
    assert mask_url('""') == "<not set>"
    assert mask_url("   ") == "<not set>"
    assert mask_url("not_a_url") == "<invalid url>"

    # Plain password
    url1 = "postgresql://myuser:supersecret@db.render.com:5432/procdb?sslmode=require"
    masked1 = mask_url(url1)
    assert "supersecret" not in masked1
    assert masked1 == "postgresql://myuser:***@db.render.com:5432/procdb?sslmode=require"

    # Percent-encoded password
    url2 = "postgres://admin_usr:p%40ss%3Aword%21@dpg-abc123xyz.oregon-postgres.render.com/production"
    masked2 = mask_url(url2)
    assert "p%40ss" not in masked2
    assert "word" not in masked2
    assert masked2 == "postgres://admin_usr:***@dpg-abc123xyz.oregon-postgres.render.com/production"

    # No password provided
    url3 = "postgresql://readonly_user@localhost:5432/devdb"
    masked3 = mask_url(url3)
    assert masked3 == "postgresql://readonly_user@localhost:5432/devdb"

    # No user or password
    url4 = "postgresql://localhost:5432/devdb"
    assert mask_url(url4) == "postgresql://localhost:5432/devdb"

    # Sensitive query parameters masked
    url5 = "postgresql://user:pass@host:5432/db?token=secrettoken&sslmode=require"
    masked5 = mask_url(url5)
    assert "secrettoken" not in masked5
    assert "pass" not in masked5
    assert "token=%2A%2A%2A" in masked5 or "token=***" in masked5


# =============================================================================
# 3. parse_and_validate_url tests
# =============================================================================


def test_parse_and_validate_url_valid_cases() -> None:
    # Standard PostgreSQL URL
    t1 = parse_and_validate_url("postgresql://app:mypass@db.host.com:5432/dbname", "DATABASE_URL")
    assert t1.hostname == "db.host.com"
    assert t1.port == 5432
    assert t1.dbname == "dbname"
    assert t1.username == "app"
    assert "sslmode=require" in t1.normalized_url

    # Render URL with 'postgres://' scheme and default port
    t2 = parse_and_validate_url("postgres://admin:pass@dpg-12345.render.com/my_db", "DATABASE_ADMIN_URL")
    assert t2.hostname == "dpg-12345.render.com"
    assert t2.port == 5432
    assert t2.dbname == "my_db"
    assert t2.normalized_url.startswith("postgresql://")
    assert "sslmode=require" in t2.normalized_url

    # SQLAlchemy scheme
    t3 = parse_and_validate_url("postgresql+psycopg://user:pass@remote:5433/prod", "TEST_URL")
    assert t3.port == 5433
    assert t3.dbname == "prod"
    assert t3.normalized_url.startswith("postgresql://")

    # Localhost does not force sslmode=require
    t4 = parse_and_validate_url("postgresql://user:pass@localhost:5432/localdb", "LOCAL_URL")
    assert "sslmode=require" not in t4.normalized_url

    # Preserves explicit sslmode
    t5 = parse_and_validate_url(
        "postgresql://user:pass@remote:5432/db?sslmode=verify-full&application_name=test",
        "TEST_URL",
    )
    assert "sslmode=verify-full" in t5.normalized_url
    assert "application_name=test" in t5.normalized_url


def test_parse_and_validate_url_rejects_missing_and_malformed() -> None:
    # Missing / empty
    with pytest.raises(ValueError, match="empty or unprovided"):
        parse_and_validate_url("", "DATABASE_URL")
    with pytest.raises(ValueError, match="empty or unprovided"):
        parse_and_validate_url('""', "DATABASE_URL")
    with pytest.raises(ValueError, match="empty or unprovided"):
        parse_and_validate_url("   ", "DATABASE_URL")

    # Non-PostgreSQL scheme
    with pytest.raises(ValueError, match="Invalid.*scheme"):
        parse_and_validate_url("sqlite:///./local.db", "DATABASE_URL")
    with pytest.raises(ValueError, match="Invalid.*scheme"):
        parse_and_validate_url("mysql://user:pass@host/db", "DATABASE_URL")
    with pytest.raises(ValueError, match="Invalid.*scheme"):
        parse_and_validate_url("host:5432/dbname", "DATABASE_URL")
    with pytest.raises(ValueError, match="missing a scheme"):
        parse_and_validate_url("plainstringnodotsorcolons", "DATABASE_URL")

    # Missing hostname
    with pytest.raises(ValueError, match="missing a hostname"):
        parse_and_validate_url("postgresql:///dbname", "DATABASE_URL")

    # Missing database name
    with pytest.raises(ValueError, match="missing a database name"):
        parse_and_validate_url("postgresql://user:pass@host:5432/", "DATABASE_URL")
    with pytest.raises(ValueError, match="missing a database name"):
        parse_and_validate_url("postgresql://user:pass@host:5432", "DATABASE_URL")


# =============================================================================
# 4. compare_targets tests (Never reports YES on connection failure)
# =============================================================================


def test_compare_targets_reports_unknown_on_connection_failure() -> None:
    t_admin = parse_and_validate_url("postgresql://admin:p1@host:5432/db", "ADMIN")
    t_app = parse_and_validate_url("postgresql://app:p2@host:5432/db", "APP")

    # Both failed
    res_both_failed: tuple[str, str] = compare_targets(
        t_admin,
        t_app,
        {"connection_status": "FAILED", "error": "OperationalError: connection refused"},
        {"connection_status": "FAILED", "error": "OperationalError: connection refused"},
    )
    assert res_both_failed[0].startswith("UNKNOWN")
    assert "YES" not in res_both_failed[0]

    # Admin succeeded, App failed
    res_app_failed = compare_targets(
        t_admin,
        t_app,
        {"connection_status": "SUCCESS", "database": "db"},
        {"connection_status": "FAILED", "error": "ProgrammingError: auth failed"},
    )
    assert res_app_failed[0].startswith("UNKNOWN")
    assert "YES" not in res_app_failed[0]
    assert "App (ProgrammingError: auth failed)" in res_app_failed[1]


def test_compare_targets_reports_yes_when_connected_and_matching() -> None:
    t_admin = parse_and_validate_url("postgresql://admin:p1@dpg-xyz.render.com:5432/my_db", "ADMIN")
    t_app = parse_and_validate_url("postgresql://app:p2@dpg-xyz.render.com:5432/my_db", "APP")

    status, detail = compare_targets(
        t_admin,
        t_app,
        {"connection_status": "SUCCESS", "database": "my_db"},
        {"connection_status": "SUCCESS", "database": "my_db"},
    )
    assert status == "YES (same database)"
    assert "my_db" in detail
    assert "dpg-xyz.render.com:5432" in detail


def test_compare_targets_reports_mismatch_when_different_targets() -> None:
    # Different databases
    t_admin = parse_and_validate_url("postgresql://admin:p1@host:5432/db_admin", "ADMIN")
    t_app = parse_and_validate_url("postgresql://app:p2@host:5432/db_app", "APP")

    status, detail = compare_targets(
        t_admin,
        t_app,
        {"connection_status": "SUCCESS", "database": "db_admin"},
        {"connection_status": "SUCCESS", "database": "db_app"},
    )
    assert status == "NO (MISMATCH!)"
    assert "Database mismatch" in detail


def test_compare_targets_single_role() -> None:
    t_admin = parse_and_validate_url("postgresql://admin:p1@host:5432/db", "ADMIN")
    status, _ = compare_targets(t_admin, None, {"connection_status": "SUCCESS"}, {})
    assert "single role" in status


# =============================================================================
# 5. resolve_urls tests
# =============================================================================


def test_resolve_urls_cli_overrides_env_and_strips_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_ADMIN_URL", '"postgresql://env_admin:p@host/db"')
    monkeypatch.setenv("DATABASE_URL", "'postgresql://env_app:p@host/db'")

    # Both from env
    admin, app = resolve_urls("", "")
    assert admin == "postgresql://env_admin:p@host/db"
    assert app == "postgresql://env_app:p@host/db"

    # CLI overrides env
    admin_cli, app_cli = resolve_urls(
        "postgresql://cli_admin:p@host/db",
        "postgresql://cli_app:p@host/db",
    )
    assert admin_cli == "postgresql://cli_admin:p@host/db"
    assert app_cli == "postgresql://cli_app:p@host/db"


# =============================================================================
# 6. inspect_role error handling (does not leak credentials or crash)
# =============================================================================


def test_inspect_role_handles_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg

    target = parse_and_validate_url("postgresql://secretuser:secretpass@fakehost.render.com:5432/fakedb", "ADMIN")

    def mock_connect(*_args: Any, **_kwargs: Any) -> Any:
        raise psycopg.OperationalError("could not translate host name 'fakehost.render.com' to address")

    monkeypatch.setattr(psycopg, "connect", mock_connect)

    result = inspect_role("Test Admin", target)
    assert result["connection_status"] == "FAILED"
    assert "OperationalError" in str(result["error"])
    assert "secretpass" not in str(result["masked_url"])


# =============================================================================
# 7. main CLI exit code tests
# =============================================================================


def test_main_exits_1_when_no_urls_provided(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("DATABASE_ADMIN_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    exit_code = main([])
    assert exit_code == 1
    captured = capsys.readouterr().out
    assert "Neither DATABASE_ADMIN_URL nor DATABASE_URL was provided" in captured


def test_main_exits_1_when_url_is_malformed(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--admin", "sqlite:///invalid.db"])
    assert exit_code == 1
    captured = capsys.readouterr().out
    assert "VALIDATION ERROR" in captured
    assert "Invalid DATABASE_ADMIN_URL scheme 'sqlite'" in captured

