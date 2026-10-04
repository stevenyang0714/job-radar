from jobradar import cli
from jobradar.config import REQUIRED_ENV


def test_copy_secrets_copies_each_value_then_clears_clipboard(monkeypatch, capsys):
    values = {name: f" value-{i} " for i, name in enumerate(REQUIRED_ENV)}
    copied = []
    monkeypatch.setattr(cli, "dotenv_values", lambda path: values)
    monkeypatch.setattr(cli.subprocess, "run", lambda cmd, input, check: copied.append(input.decode()))
    monkeypatch.setattr("builtins.input", lambda prompt: "")

    assert cli.cmd_copy_secrets(None) == 0

    assert copied == [f"value-{i}" for i in range(len(REQUIRED_ENV))] + [""]
    assert "value-" not in capsys.readouterr().out  # 不在畫面上顯示金鑰


def test_copy_secrets_stops_when_env_incomplete(monkeypatch):
    monkeypatch.setattr(cli, "dotenv_values", lambda path: {"SUPABASE_URL": "x"})
    assert cli.cmd_copy_secrets(None) == 1
