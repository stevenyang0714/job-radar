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


def test_defaults_apply_and_exclude_is_merged(tmp_path):
    path = _write_sources(
        tmp_path,
        """
defaults:
  locations: [Taiwan, Singapore]
  keywords: [AI]
  exclude: [intern]
sources:
  - {name: a, company: A, url: https://jobs.lever.co/a}
  - {name: b, company: B, url: https://jobs.lever.co/b, locations: [Hong Kong], keywords: [], exclude: [sales, intern]}
""",
    )
    a, b = load_sources(path)
    assert (a.locations, a.keywords, a.exclude) == (["Taiwan", "Singapore"], ["AI"], ["intern"])
    assert (b.locations, b.keywords, b.exclude) == (["Hong Kong"], [], ["intern", "sales"])


def test_groups_sit_between_defaults_and_source(tmp_path):
    path = _write_sources(
        tmp_path,
        """
defaults:
  locations: [Taiwan]
  exclude: [intern]
groups:
  tech: {keywords: [account manager], exclude: [engineer]}
  healthcare: {keywords: [digital], locations: [Singapore]}
sources:
  - {name: a, company: A, url: https://jobs.lever.co/a, group: tech, exclude: [director]}
  - {name: b, company: B, url: https://jobs.lever.co/b, group: healthcare}
  - {name: c, company: C, url: https://jobs.lever.co/c, group: healthcare, keywords: [market access]}
""",
    )
    a, b, c = load_sources(path)
    assert (a.group, a.locations, a.keywords, a.exclude) == ("tech", ["Taiwan"], ["account manager"], ["intern", "engineer", "director"])
    assert (b.locations, b.keywords, b.exclude) == (["Singapore"], ["digital"], ["intern"])
    assert c.keywords == ["market access"]


def test_exclude_description_accumulates_across_layers(tmp_path):
    path = _write_sources(
        tmp_path,
        """
defaults: {exclude_description: [Singaporeans only]}
groups: {finance: {exclude_description: [PR only]}}
sources:
  - {name: a, company: A, url: https://jobs.lever.co/a, group: finance, exclude_description: [citizens only]}
""",
    )
    [a] = load_sources(path)
    assert a.exclude_description == ["Singaporeans only", "PR only", "citizens only"]


def test_unknown_group_rejected(tmp_path):
    path = _write_sources(
        tmp_path,
        "groups: {tech: {}}\nsources:\n  - {name: a, company: A, url: https://jobs.lever.co/a, group: pharma}\n",
    )
    with pytest.raises(ConfigError, match="pharma"):
        load_sources(path)


def test_unknown_defaults_field_rejected(tmp_path):
    path = _write_sources(tmp_path, "defaults: {location: [Taiwan]}\nsources: []\n")
    with pytest.raises(ConfigError, match="defaults"):
        load_sources(path)


def test_missing_env_listed(tmp_path, monkeypatch):
    for name in REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    with pytest.raises(ConfigError) as exc:
        load_settings(tmp_path / "no.env")
    assert "SUPABASE_URL" not in str(exc.value)
    assert "OPENAI_API_KEY" in str(exc.value)
