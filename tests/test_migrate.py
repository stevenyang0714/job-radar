import pytest

from jobradar.config import ConfigError
from jobradar.migrate import database_url, redact


def test_empty_url_explains_where_to_find_it(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    with pytest.raises(ConfigError, match="Session pooler"):
        database_url(tmp_path / "missing.env")


def test_placeholder_password_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://postgres.abc:[YOUR-PASSWORD]@host:5432/postgres")
    with pytest.raises(ConfigError, match="YOUR-PASSWORD"):
        database_url(tmp_path / "missing.env")


def test_bracketed_password_rejected_and_redacted(monkeypatch, tmp_path):
    url = "postgresql://postgres.abc:[s3cret]@aws-0.pooler.supabase.com:5432/postgres"
    monkeypatch.setenv("SUPABASE_DB_URL", url)
    with pytest.raises(ConfigError, match="方括號"):
        database_url(tmp_path / "missing.env")
    assert "s3cret" not in redact(f"failed: {url}", url)


def test_redact_hides_plain_and_encoded_password():
    url = "postgresql://postgres.abc:p%40ss%23word@aws-0.pooler.supabase.com:5432/postgres"
    message = "failed for postgresql://postgres.abc:p%40ss%23word@host (secret p@ss#word)"
    redacted = redact(message, url)
    assert "p%40ss%23word" not in redacted and "p@ss#word" not in redacted
    assert redacted.count("****") == 2
