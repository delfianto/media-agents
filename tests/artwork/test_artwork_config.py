import pytest

from psammophis.artwork import config


def test_shared_tmdb_key_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("ARTWORK_TMDB_API_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text("TMDB_API_KEY=shared\n", encoding="utf-8")
    loaded = config.load_config(path)
    assert loaded.tmdb_api_key == "shared"
    assert loaded.media_server == "plex"


def test_missing_key_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("ARTWORK_TMDB_API_KEY", raising=False)
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    with pytest.raises(config.ConfigError):
        config.load_config(tmp_path / "missing")
