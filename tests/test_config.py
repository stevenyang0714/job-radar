import pytest

from jobradar.config import REQUIRED_ENV, ConfigError, load_settings, load_sources


def _write_sources(tmp_path, text):
    path = tmp_path / "sources.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_repo_sources_yaml_is_valid():
    sources = load_sources()
    assert {s.name for s in sources} >= {"abbott"}


def test_source_defaults(tmp_path):
    path = _write_sources(tmp_path, "sources:\n  - {name: acme, company: Acme, url: https://jobs.lever.co/acme}\n")
    [source] = load_sources(path)
    assert source.keywords == [] and source.exclude == [] and source.type is None


def test_duplicate_names_rejected(tmp_path):
    entry = "  - {name: acme, company: Acme, url: https://jobs.lever.co/acme}\n"
    path = _write_sources(tmp_path, "sources:\n" + entry * 2)
    with pytest.raises(ConfigError, match="重複"):
        load_sources(path)


@pytest.mark.parametrize(
    "extra",
    [
        "keyword: [python]",  # 欄位名稱拼錯
        "type: indeed",  # 不支援的類型
    ],
)
def test_invalid_fields_rejected(tmp_path, extra):
    path = _write_sources(
        tmp_path, f"sources:\n  - {{name: acme, company: Acme, url: https://jobs.lever.co/acme, {extra}}}\n"
    )
    with pytest.raises(ConfigError, match="第 1 個來源"):
        load_sources(path)


def test_missing_env_listed(tmp_path, monkeypatch):
    for name in REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    with pytest.raises(ConfigError) as exc:
        load_settings(tmp_path / "no.env")
    assert "SUPABASE_URL" not in str(exc.value)
    assert "OPENAI_API_KEY" in str(exc.value)
