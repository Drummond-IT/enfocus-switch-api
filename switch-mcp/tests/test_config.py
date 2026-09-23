from pathlib import Path

import pytest

from switch_mcp.config import ConfigError, Settings, read_env_file

EXAMPLE = Path(__file__).resolve().parents[1] / "config.env.example"


def test_example_config_parses_and_only_needs_real_values(tmp_path):
    s = Settings.load(EXAMPLE, environ={"HOME": str(tmp_path)})
    assert s.url == "http://switch-server.example.local:51088"
    assert (s.username, s.password) == ("ai-connector", "change-me")
    assert s.allow_write is False and s.allow_flow_control is False and s.upload_dirs == []
    errors, warnings = s.validate()
    assert errors == []
    assert any("plain http to another machine" in w for w in warnings)


def test_environment_overrides_file(tmp_path):
    f = tmp_path / "c.env"
    f.write_text('SWITCH_URL="https://a.example:51088"\nexport SWITCH_USERNAME=file-user\nSWITCH_PASSWORD=p w # x\n')
    s = Settings.load(f, environ={"SWITCH_USERNAME": "env-user"})
    assert (s.url, s.username, s.password) == ("https://a.example:51088", "env-user", "p w # x")
    assert s.env_file == f.resolve()


def test_env_file_from_variable_and_missing_file(tmp_path):
    f = tmp_path / "c.env"
    f.write_text("SWITCH_USERNAME=u\n")
    assert Settings.load(environ={"SWITCH_ENV_FILE": str(f)}).username == "u"
    with pytest.raises(ConfigError, match="not found"):
        Settings.load(tmp_path / "nope.env", environ={})


def test_password_file(tmp_path):
    pw = tmp_path / "pw.txt"
    pw.write_text("hunter2\n")
    s = Settings.from_env({"SWITCH_PASSWORD_FILE": str(pw)})
    assert s.password == "hunter2"
    assert "hunter2" not in repr(s)


def test_bad_values_are_explained(tmp_path):
    with pytest.raises(ConfigError, match="SWITCH_ALLOW_WRITE must be true or false"):
        Settings.from_env({"SWITCH_ALLOW_WRITE": "maybe"})
    with pytest.raises(ConfigError, match="expected KEY=value"):
        bad = tmp_path / "bad.env"
        bad.write_text("just some words\n")
        read_env_file(bad)
    s = Settings.from_env({"SWITCH_URL": "https://h:1/api", "SWITCH_USERNAME": "u", "SWITCH_PASSWORD": "p",
                           "SWITCH_VERIFY_TLS": "false"})
    errors, _ = s.validate()
    assert any("must not contain a path" in e for e in errors)


def test_localhost_http_is_not_warned():
    s = Settings.from_env({"SWITCH_URL": "http://127.0.0.1:51088", "SWITCH_USERNAME": "u", "SWITCH_PASSWORD": "p"})
    assert s.validate() == ([], [])


def test_tls_options():
    s = Settings.from_env({"SWITCH_URL": "https://h:51088", "SWITCH_USERNAME": "u", "SWITCH_PASSWORD": "p",
                           "SWITCH_VERIFY_TLS": "false"})
    assert s.tls_verify is False
    assert any("certificate checks are OFF" in w for w in s.validate()[1])
    assert Settings.from_env({"SWITCH_CA_BUNDLE": "/x/ca.pem"}).tls_verify == "/x/ca.pem"
