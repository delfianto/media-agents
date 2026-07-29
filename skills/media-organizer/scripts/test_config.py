from __future__ import annotations

import pytest
from mediaorganizer import config


def test_parse_dotenv_basic():
    text = "KEY_A=value_a\nKEY_B = value_b \n"
    assert config.parse_dotenv(text) == {"KEY_A": "value_a", "KEY_B": "value_b"}


def test_parse_dotenv_ignores_comments_and_blank_lines():
    text = "\n# a comment\nKEY=value\n   \n# KEY2=commented_out\n"
    assert config.parse_dotenv(text) == {"KEY": "value"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('KEY="quoted value"', "quoted value"),
        ("KEY='quoted value'", "quoted value"),
        ("KEY=unquoted value", "unquoted value"),
        ("KEY=\"mismatched'", "\"mismatched'"),
    ],
)
def test_parse_dotenv_quote_stripping(raw, expected):
    assert config.parse_dotenv(raw)["KEY"] == expected


def test_parse_dotenv_value_containing_equals_sign():
    assert config.parse_dotenv("KEY=a=b=c")["KEY"] == "a=b=c"


def _write_env(tmp_path, contents):
    env_path = tmp_path / ".env"
    env_path.write_text(contents, encoding="utf-8")
    return env_path


def test_load_config_missing_tmdb_key_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIAORGANIZER_TMDB_API_KEY", raising=False)
    env_path = _write_env(
        tmp_path,
        "MEDIAORGANIZER_MOVIES_DIR=/Movies\n"
        "MEDIAORGANIZER_TV_SHOWS_DIR=/TV\n"
        "MEDIAORGANIZER_INBOX_DIR=/Inbox\n",
    )
    with pytest.raises(config.ConfigError, match="TMDB_API_KEY"):
        config.load_config(env_path)


def test_load_config_missing_directories_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIAORGANIZER_MOVIES_DIR", raising=False)
    env_path = _write_env(tmp_path, "MEDIAORGANIZER_TMDB_API_KEY=abc123\n")
    with pytest.raises(config.ConfigError, match="MOVIES_DIR"):
        config.load_config(env_path)


def test_load_config_invalid_server_raises(tmp_path):
    env_path = _write_env(
        tmp_path,
        "MEDIAORGANIZER_TMDB_API_KEY=abc123\n"
        "MEDIAORGANIZER_MOVIES_DIR=/Movies\n"
        "MEDIAORGANIZER_TV_SHOWS_DIR=/TV\n"
        "MEDIAORGANIZER_INBOX_DIR=/Inbox\n"
        "MEDIAORGANIZER_SERVER=emby\n",
    )
    with pytest.raises(config.ConfigError, match="SERVER"):
        config.load_config(env_path)


def test_load_config_defaults(tmp_path):
    env_path = _write_env(
        tmp_path,
        "MEDIAORGANIZER_TMDB_API_KEY=abc123\n"
        "MEDIAORGANIZER_MOVIES_DIR=/Movies\n"
        "MEDIAORGANIZER_TV_SHOWS_DIR=/TV\n"
        "MEDIAORGANIZER_INBOX_DIR=/Inbox\n",
    )
    cfg = config.load_config(env_path)
    assert cfg.media_server == "plex"
    assert cfg.subtitle_languages == ("en",)
    assert cfg.min_confidence == pytest.approx(0.75)
    assert cfg.opensubtitles_api_key is None


def test_load_config_subtitle_languages_split_and_stripped(tmp_path):
    env_path = _write_env(
        tmp_path,
        "MEDIAORGANIZER_TMDB_API_KEY=abc123\n"
        "MEDIAORGANIZER_MOVIES_DIR=/Movies\n"
        "MEDIAORGANIZER_TV_SHOWS_DIR=/TV\n"
        "MEDIAORGANIZER_INBOX_DIR=/Inbox\n"
        "MEDIAORGANIZER_SUBTITLE_LANGUAGES= en, es , fr\n",
    )
    cfg = config.load_config(env_path)
    assert cfg.subtitle_languages == ("en", "es", "fr")


def test_real_environment_overrides_dotenv_file(tmp_path, monkeypatch):
    env_path = _write_env(
        tmp_path,
        "MEDIAORGANIZER_TMDB_API_KEY=from_dotenv\n"
        "MEDIAORGANIZER_MOVIES_DIR=/Movies\n"
        "MEDIAORGANIZER_TV_SHOWS_DIR=/TV\n"
        "MEDIAORGANIZER_INBOX_DIR=/Inbox\n",
    )
    monkeypatch.setenv("MEDIAORGANIZER_TMDB_API_KEY", "from_real_env")
    cfg = config.load_config(env_path)
    assert cfg.tmdb_api_key == "from_real_env"


def test_missing_dotenv_file_falls_back_to_real_environment_only(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIAORGANIZER_TMDB_API_KEY", "abc123")
    monkeypatch.setenv("MEDIAORGANIZER_MOVIES_DIR", "/Movies")
    monkeypatch.setenv("MEDIAORGANIZER_TV_SHOWS_DIR", "/TV")
    monkeypatch.setenv("MEDIAORGANIZER_INBOX_DIR", "/Inbox")
    cfg = config.load_config(tmp_path / "does-not-exist.env")
    assert cfg.tmdb_api_key == "abc123"
