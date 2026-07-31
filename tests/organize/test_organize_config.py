import pytest

from psammophis.organize import config


def _env(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_shared_tmdb_fallback_and_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("ORGANIZE_TMDB_API_KEY", raising=False)
    path = _env(
        tmp_path,
        "TMDB_API_KEY=shared\n"
        "ORGANIZE_MOVIES_DIR=/Movies\n"
        "ORGANIZE_TV_SHOWS_DIR=/TV\n"
        "ORGANIZE_INBOX_DIR=/Inbox\n",
    )
    loaded = config.load_config(path)
    assert loaded.tmdb_api_key == "shared"
    assert loaded.media_server == "plex"
    assert loaded.min_confidence == pytest.approx(0.75)


def test_skill_specific_key_wins(tmp_path):
    path = _env(
        tmp_path,
        "TMDB_API_KEY=shared\n"
        "ORGANIZE_TMDB_API_KEY=specific\n"
        "ORGANIZE_MOVIES_DIR=/Movies\n"
        "ORGANIZE_TV_SHOWS_DIR=/TV\n"
        "ORGANIZE_INBOX_DIR=/Inbox\n",
    )
    assert config.load_config(path).tmdb_api_key == "specific"


def test_missing_directories_raises(tmp_path):
    path = _env(tmp_path, "TMDB_API_KEY=shared\n")
    with pytest.raises(config.ConfigError, match="MOVIES_DIR"):
        config.load_config(path)
